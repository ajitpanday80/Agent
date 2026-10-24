"""Priority and escalation rules. Settings may be supplied by the Flask console or environment.

Priority = ITIL-style impact x urgency matrix, then overrides. The LLM only reports FACTS
(scope, severity, flags); this module decides priority and action.

The SLA numbers are placeholder defaults modelled on one published example
(https://blog.invgate.com/itil-priority-matrix). They are NOT an ITIL standard. Replace them with
the targets in your own client contracts.
"""
import os
import sqlite3
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# priority -> (acknowledge within minutes, resolve within hours; calendar hours for simplicity)
DEFAULT_SLA = {1: (10, 4), 2: (15, 8), 3: (60, 48), 4: (240, 120)}


def _sla_from_env() -> dict[int, tuple[int, int]] | None:
    """MSP_SLA="1:10/4,2:15/8,3:60/48,4:240/120"  (priority:ack_minutes/resolve_hours)."""
    raw = os.getenv("MSP_SLA", "").strip()
    if not raw:
        return None
    out = {}
    for part in raw.split(","):
        pr, rest = part.split(":")
        ack, res = rest.split("/")
        out[int(pr)] = (int(ack), int(res))
    if sorted(out) != [1, 2, 3, 4]:
        raise ValueError("MSP_SLA must define priorities 1, 2, 3 and 4")
    return out


SLA = _sla_from_env() or DEFAULT_SLA
SLA_IS_DEFAULT = _sla_from_env() is None

# (impact, urgency) -> priority. 1 = highest.
MATRIX = {
    (1, 1): 1, (1, 2): 2, (1, 3): 3,
    (2, 1): 2, (2, 2): 3, (2, 3): 4,
    (3, 1): 3, (3, 2): 4, (3, 3): 4,
}

SCOPES = ("single_user", "several_users", "whole_company")
SEVERITIES = ("stopped", "degraded", "minor")
CATEGORIES = ("email", "printing", "network_vpn", "performance", "access", "security", "software", "hardware", "other")

# Per CISA's ransomware guidance: isolate from the network, but do not power off by default
# (powering off can destroy evidence in memory). https://www.cisa.gov/stopransomware/ive-been-hit-ransomware
SECURITY_INSTRUCTIONS = (
    "Ask the caller to disconnect the affected computer from the network (unplug the network cable or turn off "
    "Wi-Fi) but NOT to power it off, and not to delete, open or forward anything suspicious. "
    "Do not discuss paying any ransom."
)


def _runtime_setting(key: str, env_name: str, default: str) -> str:
    """Prefer admin-console settings so voice workers share routing policy across processes."""
    return _stored_settings((key,)).get(key, os.getenv(env_name, default))


def _stored_settings(keys: tuple[str, ...]) -> dict[str, str]:
    if not keys:
        return {}
    try:
        from . import db
        path = Path(os.getenv("MSP_DB") or db.DEFAULT_PATH)
        if not path.exists():
            return {}
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            marks = ",".join("?" for _ in keys)
            return {r["key"]: r["value"] for r in conn.execute(
                f"SELECT key,value FROM app_settings WHERE key IN ({marks})", keys)}
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


def sla_for(priority: int) -> tuple[int, int]:
    ack, resolve = SLA[priority]
    rows = _stored_settings((f"p{priority}_ack", f"p{priority}_resolve"))
    try:
        ack = int(rows.get(f"p{priority}_ack", ack))
        resolve = int(rows.get(f"p{priority}_resolve", resolve))
    except ValueError:
        return SLA[priority]
    return ack, resolve

@dataclass
class Decision:
    priority: int
    impact: int
    urgency: int
    action: str  # ticket | ticket_callback_verify | transfer_to_human | page_oncall | callback_next_business_day
    requires_verification: bool
    security: bool
    ack_minutes: int
    resolve_hours: int
    reasons: list[str] = field(default_factory=list)
    caller_instructions: str = ""

    def say_to_caller(self) -> str:
        if self.action == "transfer_to_human":
            return "Tell the caller the ticket is in the staff follow-up queue. A live transfer is not available on this line."
        if self.action == "page_oncall":
            return "Tell the caller the ticket is marked urgent for staff follow-up. Do not promise a callback time."
        if self.action == "voicemail":
            return "Tell the caller the request is in the helpdesk intake queue. No voicemail recording was made."
        if self.action == "ticket_callback_verify":
            return "Tell the caller staff follow-up is required to verify their identity before any account change. Do not promise a callback time."
        if self.action == "callback_next_business_day":
            return "Tell the caller the ticket is in the staff follow-up queue. Do not promise a callback time."
        return "Tell the caller the ticket is recorded in Zoho Desk for staff follow-up."


def in_business_hours(now: datetime | None = None) -> bool:
    tz = ZoneInfo(_runtime_setting("timezone", "MSP_TZ", "Australia/Sydney"))
    now = (now or datetime.now(tz)).astimezone(tz)
    if _runtime_setting("coverage_mode", "MSP_COVERAGE_MODE", "weekdays") == "24x7":
        return True
    start, end = _runtime_setting("hours", "MSP_HOURS", "08:00-18:00").split("-")
    if now.weekday() >= 5:
        return False
    return start <= now.strftime("%H:%M") < end


def _bump(priority: int) -> int:
    return max(1, priority - 1)


def _fmt_minutes(m: int) -> str:
    if m < 60:
        return f"{m} minutes"
    h = m // 60
    return f"{h} hour" + ("s" if h > 1 else "")


def decide(
    *,
    category: str,
    scope: str,
    severity: str,
    workaround_available: bool = False,
    security_concern: bool = False,
    data_loss: bool = False,
    caller_requests_human: bool = False,
    caller_hostile: bool = False,
    is_vip: bool = False,
    caller_recognized: bool = True,
    prior_contacts: int = 0,
    now: datetime | None = None,
) -> Decision:
    reasons: list[str] = []
    impact = {"whole_company": 1, "several_users": 2}.get(scope, 3)
    urgency = {"stopped": 1, "degraded": 2}.get(severity, 3)
    if severity == "stopped" and workaround_available:
        urgency = 2
        reasons.append("work stopped but a workaround exists")
    priority = MATRIX[(impact, urgency)]
    reasons.append(f"impact {impact} x urgency {urgency} = P{priority}")

    security = security_concern or data_loss or category == "security"
    if security:
        priority = 1
        reasons.append("security incident or data loss: always P1")
    if is_vip and priority > 1:
        priority = _bump(priority)
        reasons.append("VIP caller: bumped one level")
    if prior_contacts >= 2 and priority > 1:
        priority = _bump(priority)
        reasons.append(f"caller has contacted us {prior_contacts + 1} times about this: bumped one level")

    requires_verification = category == "access"
    if requires_verification:
        reasons.append("account/access change: never done by the AI; technician must verify identity")
    if not caller_recognized:
        reasons.append("caller not found in client records: share no account details")
        if category == "access":
            requires_verification = True

    in_hours = in_business_hours(now)
    wants_human = caller_requests_human or caller_hostile or prior_contacts >= 2
    if caller_requests_human:
        reasons.append("caller asked for a person")
    if caller_hostile:
        reasons.append("caller is distressed or hostile")

    # Edit the rules below to change who gets paged. P1 and P2 are paged after hours here.
    if priority <= 2 or security:
        if in_hours:
            action = "transfer_to_human"
        elif not security and _runtime_setting("after_hours_mode", "MSP_AFTER_HOURS_MODE", "on_call") == "voicemail":
            action = "voicemail"
        else:
            action = "page_oncall"
    elif wants_human:
        action = "transfer_to_human" if in_hours else "callback_next_business_day"
    elif requires_verification:
        action = "ticket_callback_verify"
    else:
        action = "ticket"
    if requires_verification and action in ("ticket",):
        action = "ticket_callback_verify"

    ack, resolve = sla_for(priority)
    return Decision(
        priority=priority, impact=impact, urgency=urgency, action=action,
        requires_verification=requires_verification, security=security,
        ack_minutes=ack, resolve_hours=resolve, reasons=reasons,
        caller_instructions=SECURITY_INSTRUCTIONS if security else "",
    )


def due_times(priority: int, now: datetime) -> tuple[datetime, datetime]:
    ack, resolve = sla_for(priority)
    return now + timedelta(minutes=ack), now + timedelta(hours=resolve)
