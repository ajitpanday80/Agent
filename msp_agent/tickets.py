"""Zoho Desk ticket operations plus local call routing, repeat-call links, and incident notices."""
import hashlib
import re
import sqlite3
from datetime import datetime, timezone

from . import escalation

STATUSES = ("open", "in_progress", "waiting_on_customer", "resolved", "closed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ref(text: str | int) -> int | None:
    """Normalize a Zoho ticket ID or human ticket number; Zoho IDs remain authoritative."""
    value = str(text).strip()
    m = re.fullmatch(r"(?i)(?:T[- ]?)?(\d+)", value)
    return int(m.group(1)) if m else None


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def identify_caller(conn: sqlite3.Connection, name: str, company: str, callback_number: str = "") -> dict:
    """Match an exact name, account, and phone against Zoho contacts. This is not identity verification."""
    from . import zoho
    try:
        row = zoho.match_contact(name, company, callback_number)
    except Exception:
        row = None
    if row:
        acct = row.get("account") or {}
        flag = conn.execute("SELECT is_vip FROM contact_flags WHERE zoho_contact_id=?",
                            (str(row.get("id", "")),)).fetchone()
        return {"recognized": True, "contact_id": row.get("id"), "company_id": acct.get("id"),
                "company_name": acct.get("accountName", company),
                "name": " ".join(p for p in (row.get("firstName"), row.get("lastName")) if p),
                "is_vip": bool(flag["is_vip"] if flag else row.get("isVIP", row.get("isVip", False))),
                "phone_matches_record": True,
                "zoho_contact": row}
    return {"recognized": False, "contact_id": None, "company_id": None, "company_name": company,
            "name": name, "is_vip": False, "phone_matches_record": False}


def active_incidents(conn: sqlite3.Connection, category: str | None = None) -> list[sqlite3.Row]:
    sql, args = "SELECT * FROM incidents WHERE status='active'", []
    if category:
        sql += " AND category=?"
        args.append(category)
    return conn.execute(sql, args).fetchall()


def create_incident(conn: sqlite3.Connection, title: str, category: str) -> int:
    cur = conn.execute("INSERT INTO incidents(title,category,created_at) VALUES (?,?,?)",
                       (title, category, _iso(_now())))
    conn.commit()
    return cur.lastrowid


def resolve_incident(conn: sqlite3.Connection, incident_id: int) -> None:
    conn.execute("UPDATE incidents SET status='resolved', resolved_at=? WHERE id=?", (_iso(_now()), incident_id))
    conn.commit()


def add_note(conn: sqlite3.Connection, ticket_id: int, author: str, body: str) -> None:
    from . import zoho
    ticket = get(conn, ticket_id)
    if not ticket:
        raise ValueError("Zoho ticket not found")
    zoho.add_comment(ticket["id"], f"{author}: {body}", public=False)


def create(
    conn: sqlite3.Connection, *, caller_name: str, company_name: str, callback_number: str,
    category: str, summary: str, details: str = "", scope: str = "single_user", severity: str = "degraded",
    workaround_available: bool = False, security_concern: bool = False, data_loss: bool = False,
    caller_requests_human: bool = False, caller_hostile: bool = False, now: datetime | None = None,
) -> dict:
    """Create or update a Zoho Desk ticket; SQLite stores only the repeat-call link."""
    from . import zoho
    now = now or _now()
    caller = identify_caller(conn, caller_name, company_name, callback_number)
    identity = "|".join((str(caller.get("contact_id") or ""), caller_name.lower().strip(),
                         company_name.lower().strip(), _digits(callback_number)))
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()
    link = conn.execute("SELECT * FROM ticket_links WHERE fingerprint=? AND category=?",
                        (fingerprint, category)).fetchone()
    existing = None
    prior = 0
    if link:
        try:
            candidate = zoho.get_ticket(link["zoho_ticket_id"])
            if str(candidate.get("status") or candidate.get("statusType") or "Open").lower() not in ("closed", "resolved"):
                existing, prior = candidate, int(link["contact_count"])
        except RuntimeError as error:
            raise RuntimeError("Could not check Zoho for an existing ticket; no duplicate was created") from error
    d = escalation.decide(
        category=category, scope=scope, severity=severity, workaround_available=workaround_available,
        security_concern=security_concern, data_loss=data_loss, caller_requests_human=caller_requests_human,
        caller_hostile=caller_hostile, is_vip=caller["is_vip"], caller_recognized=caller["recognized"],
        prior_contacts=prior, now=now,
    )
    incident = next(iter(active_incidents(conn, category)), None)
    note = details + (f"\nKnown incident: {incident['title']}" if incident else "")
    local_ref = f"VOICE-{now.strftime('%Y%m%d%H%M%S')}"
    pseudo = {"ref": local_ref, "caller_name": caller_name, "company_name": company_name,
              "callback_number": callback_number, "caller_recognized": int(caller["recognized"]),
              "category": category, "priority": d.priority, "details": note, "status": "open"}
    if existing:
        old = _from_zoho(existing)
        previous_priority = old["priority"]
        d.priority = min(d.priority, previous_priority)
        if d.priority < previous_priority:
            d.reasons.append(f"repeat-call escalation raised the existing ticket from P{previous_priority} to P{d.priority}")
        else:
            d.reasons.append(f"retained the existing ticket priority P{previous_priority}")
        d.ack_minutes, d.resolve_hours = escalation.sla_for(d.priority)
        if d.priority <= 2:
            if escalation.in_business_hours(now):
                d.action = "transfer_to_human"
            elif not d.security and escalation._runtime_setting("after_hours_mode", "MSP_AFTER_HOURS_MODE", "on_call") == "voicemail":
                d.action = "voicemail"
            else:
                d.action = "page_oncall"
        clean_subject = re.sub(r"^(?:\[P[1-4]\]\s*)+", "", str(existing.get("subject", "")), flags=re.IGNORECASE)
        zoho.update_ticket(old["id"], {"priority": zoho.PRIORITY_MAP[d.priority],
                                        "subject": f"[P{d.priority}] {clean_subject}"})
        zoho.add_comment(old["id"], "Repeat call logged by the MSP voice agent.\n" + zoho._description(pseudo, d),
                         public=False)
        count = prior + 1
        conn.execute("UPDATE ticket_links SET contact_count=?,updated_at=? WHERE fingerprint=? AND category=?",
                     (count, _iso(now), fingerprint, category))
        conn.commit()
        ticket = _from_zoho(zoho.get_ticket(old["id"]))
        ticket["priority"], ticket["action"] = d.priority, d.action
        return {"ticket": ticket, "decision": d, "is_new": False, "caller": caller,
                "previous_priority": previous_priority, "incident": dict(incident) if incident else None}
    payload = {
        "subject": f"[P{d.priority}] {summary}", "departmentId": zoho._department_id(),
        "contact": zoho._contact(pseudo, None), "description": zoho._description(pseudo, d),
        "priority": zoho.PRIORITY_MAP[d.priority], "channel": "Phone", "status": "Open",
    }
    if caller.get("contact_id"):
        payload.pop("contact", None)
        payload["contactId"] = str(caller["contact_id"])
        if caller.get("company_id"):
            payload["accountId"] = str(caller["company_id"])
    response = zoho.create_ticket(payload)
    if not response.get("id"):
        raise RuntimeError(f"Zoho Desk created a ticket without returning its ID: {response}")
    ticket = _from_zoho(response)
    ticket["action"] = d.action
    conn.execute("INSERT INTO ticket_links(fingerprint,category,zoho_ticket_id,contact_count,updated_at) "
                 "VALUES(?,?,?,1,?) ON CONFLICT(fingerprint,category) DO UPDATE SET "
                 "zoho_ticket_id=excluded.zoho_ticket_id,contact_count=1,updated_at=excluded.updated_at",
                 (fingerprint, category, str(response["id"]), _iso(now)))
    conn.commit()
    return {"ticket": ticket, "decision": d, "is_new": True, "caller": caller,
            "previous_priority": None, "incident": dict(incident) if incident else None}


NEVER_SELF_RESOLVED = ("security", "access")
SENSITIVE_TERMS = ("security", "phish", "malware", "ransom", "virus", "hacked", "compromis", "stolen",
                   "suspicious", "data loss", "password", "account", "sign in", "sign-in", "login", "mfa",
                   "authenticator", "lockout", "locked out", "reset", "unlock", "bypass")


def create_resolved(
    conn: sqlite3.Connection, *, caller_name: str, company_name: str, callback_number: str, category: str,
    summary: str, resolution: str, now: datetime | None = None,
) -> dict:
    """Record a call the agent fixed with a knowledge-base step. Created already closed, always P4.
    Security and access issues are refused: those always need a technician."""
    if category in NEVER_SELF_RESOLVED:
        raise ValueError(f"{category} issues cannot be logged as resolved by the agent; create a normal ticket")
    text = f"{summary} {resolution}".lower()
    if any(term in text for term in SENSITIVE_TERMS):
        raise ValueError("security or account-access signals require a technician; create a normal ticket")
    from . import zoho
    now = now or _now()
    caller = identify_caller(conn, caller_name, company_name, callback_number)
    d = escalation.decide(category=category, scope="single_user", severity="minor",
                          caller_recognized=caller["recognized"], now=now)
    d.action = "resolved_by_agent"
    pseudo = {"ref": "VOICE-RESOLVED", "caller_name": caller_name, "company_name": company_name,
              "callback_number": callback_number, "caller_recognized": int(caller["recognized"]),
              "category": category, "priority": 4,
              "details": f"Resolved by the AI agent during the call. Fix given: {resolution}", "status": "closed"}
    payload = {"subject": f"[P4] Resolved call: {summary}",
               "departmentId": zoho._department_id(), "contact": zoho._contact(pseudo, None),
               "description": zoho._description(pseudo, d) +
                              f"\nCaller confirmed fix worked. Fix given: {resolution}",
               "priority": "Low", "channel": "Phone", "status": "Closed"}
    if caller.get("contact_id"):
        payload.pop("contact", None)
        payload["contactId"] = str(caller["contact_id"])
        if caller.get("company_id"):
            payload["accountId"] = str(caller["company_id"])
    response = zoho.create_ticket(payload)
    if not response.get("id"):
        raise RuntimeError(f"Zoho Desk created a ticket without returning its ID: {response}")
    return {"ticket": _from_zoho(response), "decision": d, "is_new": True, "caller": caller,
            "previous_priority": None, "incident": None}


def get(conn: sqlite3.Connection, ticket_id: int) -> dict | None:
    from . import zoho
    try:
        value = str(ticket_id)
        if len(value) < 12:
            found = next((t for t in zoho.list_tickets() if str(t.get("ticketNumber", "")) == value), None)
            if not found:
                return None
            raw = zoho.get_ticket(str(found["id"]))
        else:
            raw = zoho.get_ticket(value)
        return _from_zoho(raw)
    except RuntimeError:
        return None


def list_tickets(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    from . import zoho
    rows = [_from_zoho(r) for r in zoho.list_tickets()]
    if status:
        rows = [r for r in rows if r["status"] == status]
    return rows


def set_status(conn: sqlite3.Connection, ticket_id: int, status: str, assignee: str | None = None) -> None:
    from . import zoho
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    patch = {"status": zoho.ZOHO_STATUS.get(status, "Open")}
    if assignee:
        agent = next((a for a in zoho.list_agents() if a.get("email", "").lower() == assignee.lower()), None)
        if not agent:
            raise ValueError("Zoho Desk agent not found")
        patch["assigneeId"] = agent["id"]
    zoho.update_ticket(str(ticket_id), patch)


def _from_zoho(row: dict) -> dict:
    priority = {"high": 2, "medium": 3, "low": 4}.get(str(row.get("priority", "")).lower(), 3)
    pmark = re.match(r"\[P([1-4])\]", str(row.get("subject", "")), re.IGNORECASE)
    if pmark:
        priority = int(pmark.group(1))
    contact = row.get("contact") or {}
    account = row.get("account") or {}
    assignee = row.get("assignee") or {}
    description = str(row.get("description", ""))
    category_match = re.search(r"(?im)^Category:\s*([a-z0-9_]+)", description)
    action_match = re.search(r"(?im)^Category:.*?Action:\s*([a-z_]+)", description)
    caller_match = re.search(r"(?im)^Caller:\s*(.*?)\s*\((.*?)\),\s*callback\s*(.*)$", description)
    subject = re.sub(r"^(?:\[P[1-4]\]\s*)+", "", str(row.get("subject", "")), flags=re.IGNORECASE)
    tid = str(row.get("id", ""))
    return {"id": tid, "zoho_id": tid, "ref": str(row.get("ticketNumber") or tid),
            "status": str(row.get("status") or row.get("statusType") or "Open").lower().replace(" ", "_"), "priority": priority,
            "category": category_match.group(1) if category_match else row.get("category", "other"), "caller_name": " ".join(
                p for p in (contact.get("firstName"), contact.get("lastName")) if p) or
                (caller_match.group(1) if caller_match else ""),
            "company_name": account.get("accountName") or row.get("accountName") or
                            (caller_match.group(2) if caller_match else ""),
            "company_id": row.get("accountId"), "contact_id": row.get("contactId"),
            "callback_number": contact.get("phone", "") or (caller_match.group(3) if caller_match else ""),
            "summary": subject, "details": description,
            "assignee": assignee.get("name") or assignee.get("email"),
            "ack_due": row.get("dueDate", ""), "action": action_match.group(1) if action_match else "ticket",
            "notes": []}
