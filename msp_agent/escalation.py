"""Priority and escalation rules. Pure functions, no I/O, so they are easy to test and tune.

Priority = ITIL-style impact x urgency matrix, then overrides. The LLM only reports FACTS
(scope, severity, flags); this module decides priority and action.

The SLA numbers are placeholder defaults modelled on one published example
(https://blog.invgate.com/itil-priority-matrix). They are NOT an ITIL standard. Replace them with
the targets in your own client contracts.
"""
import os
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

# Outages in these categories rarely stop a business outright (there is usually a workaround such as another
# printer or a restart), so a whole-office printer problem is not P1. Edit to match your clients' reality.
NON_CRITICAL_CATEGORIES = ("printing", "performance")

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

TRANSFER_ENABLED = os.getenv("LIVE_TRANSFER_ENABLED", "false").lower() == "true"


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
        eta = _fmt_minutes(self.ack_minutes)
        if self.action == "transfer_to_human":
            if TRANSFER_ENABLED:
                return "Tell the caller you are transferring them to a technician now."
            return f"Tell the caller a technician will call them back, target within {eta}, and confirm their callback number."
        if self.action == "page_oncall":
            return f"Tell the caller the on-call engineer has been alerted and will call back, target within {eta}."
        if self.action == "ticket_callback_verify":
            return ("Tell the caller a technician will call back to verify their identity before changing anything "
                    f"on their account, target within {eta}.")
        if self.action == "callback_next_business_day":
            return "Tell the caller the office is closed and a technician will call back next business day."
        return f"Tell the caller the team will follow up, target first response within {eta}."


def in_business_hours(now: datetime | None = None) -> bool:
    tz = ZoneInfo(os.getenv("MSP_TZ", "UTC"))
    now = (now or datetime.now(tz)).astimezone(tz)
    start, end = os.getenv("MSP_HOURS", "08:00-18:00").split("-")
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
    if category in NON_CRITICAL_CATEGORIES and not security_concern:
        if impact < 2 or urgency < 2:
            reasons.append(f"{category} is a non-critical category: impact and urgency capped at 2")
        impact, urgency = max(impact, 2), max(urgency, 2)
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
        action = "transfer_to_human" if in_hours else "page_oncall"
    elif wants_human:
        action = "transfer_to_human" if in_hours else "callback_next_business_day"
    elif requires_verification:
        action = "ticket_callback_verify"
    else:
        action = "ticket"
    if requires_verification and action in ("ticket",):
        action = "ticket_callback_verify"

    ack, resolve = SLA[priority]
    return Decision(
        priority=priority, impact=impact, urgency=urgency, action=action,
        requires_verification=requires_verification, security=security,
        ack_minutes=ack, resolve_hours=resolve, reasons=reasons,
        caller_instructions=SECURITY_INSTRUCTIONS if security else "",
    )


def due_times(priority: int, now: datetime) -> tuple[datetime, datetime]:
    ack, resolve = SLA[priority]
    return now + timedelta(minutes=ack), now + timedelta(hours=resolve)
