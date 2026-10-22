"""Mock PSA ticketing: callers, tickets, notes, known incidents, escalation log."""
import re
import sqlite3
from datetime import datetime, timezone

from . import escalation

STATUSES = ("open", "in_progress", "waiting_on_customer", "resolved", "closed")
OPEN_STATUSES = ("open", "in_progress", "waiting_on_customer")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ref(ticket_id: int) -> str:
    return f"T-{1000 + ticket_id}"


def parse_ref(text: str | int) -> int | None:
    """Accepts 'T-1004', 't 1004', '1004'. Returns the internal id."""
    m = re.search(r"(\d+)", str(text))
    if not m:
        return None
    n = int(m.group(1)) - 1000
    return n if n > 0 else None


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def identify_caller(conn: sqlite3.Connection, name: str, company: str, callback_number: str = "") -> dict:
    """Match a caller to client records. 'Recognized' is NOT identity verification."""
    rows = conn.execute(
        "SELECT c.*, co.name AS company_name FROM contacts c JOIN companies co ON co.id=c.company_id "
        "WHERE lower(co.name) LIKE ?", (f"%{company.strip().lower()}%",)
    ).fetchall() if company.strip() else []
    tokens = [t for t in re.split(r"\s+", name.lower().strip()) if t]
    for row in rows:
        if tokens and all(t in row["name"].lower() for t in tokens):
            return {
                "recognized": True, "contact_id": row["id"], "company_id": row["company_id"],
                "company_name": row["company_name"], "name": row["name"], "is_vip": bool(row["is_vip"]),
                "phone_matches_record": bool(callback_number) and _digits(callback_number) == _digits(row["phone"]),
            }
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


def open_duplicate(conn: sqlite3.Connection, company_name: str, caller_name: str, category: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tickets WHERE lower(company_name)=lower(?) AND lower(caller_name)=lower(?) "
        "AND category=? AND status IN ('open','in_progress','waiting_on_customer') ORDER BY id DESC LIMIT 1",
        (company_name, caller_name, category),
    ).fetchone()


def add_note(conn: sqlite3.Connection, ticket_id: int, author: str, body: str) -> None:
    conn.execute("INSERT INTO ticket_notes(ticket_id,author,body,created_at) VALUES (?,?,?,?)",
                 (ticket_id, author, body, _iso(_now())))
    conn.execute("UPDATE tickets SET updated_at=? WHERE id=?", (_iso(_now()), ticket_id))
    conn.commit()


def create(
    conn: sqlite3.Connection, *, caller_name: str, company_name: str, callback_number: str,
    category: str, summary: str, details: str = "", scope: str = "single_user", severity: str = "degraded",
    workaround_available: bool = False, security_concern: bool = False, data_loss: bool = False,
    caller_requests_human: bool = False, caller_hostile: bool = False, now: datetime | None = None,
) -> dict:
    """Create (or update) a ticket. Priority and action come from escalation.decide, not the caller."""
    now = now or _now()
    caller = identify_caller(conn, caller_name, company_name, callback_number)
    dup = open_duplicate(conn, company_name, caller_name, category)
    prior = dup["contact_count"] if dup else 0

    d = escalation.decide(
        category=category, scope=scope, severity=severity, workaround_available=workaround_available,
        security_concern=security_concern, data_loss=data_loss, caller_requests_human=caller_requests_human,
        caller_hostile=caller_hostile, is_vip=caller["is_vip"], caller_recognized=caller["recognized"],
        prior_contacts=prior, now=now,
    )
    incident = next(iter(active_incidents(conn, category)), None)

    if dup:
        # Same person, same category, still open: this is a repeat call, not a new ticket.
        priority = min(dup["priority"], d.priority)
        conn.execute(
            "UPDATE tickets SET contact_count=contact_count+1, priority=?, action=?, updated_at=? WHERE id=?",
            (priority, d.action, _iso(now), dup["id"]),
        )
        add_note(conn, dup["id"], "voice-agent", f"Repeat call. Caller reports: {summary}. {details}".strip())
        tid, is_new = dup["id"], False
    else:
        ack_due, resolve_due = escalation.due_times(d.priority, now)
        cur = conn.execute(
            "INSERT INTO tickets(company_id,contact_id,caller_name,company_name,callback_number,category,summary,"
            "details,priority,impact,urgency,caller_recognized,requires_verification,security_flag,incident_id,"
            "action,created_at,updated_at,ack_due,resolve_due) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (caller["company_id"], caller["contact_id"], caller_name, company_name, callback_number, category,
             summary, details, d.priority, d.impact, d.urgency, int(caller["recognized"]),
             int(d.requires_verification), int(d.security), incident["id"] if incident else None, d.action,
             _iso(now), _iso(now), _iso(ack_due), _iso(resolve_due)),
        )
        tid, is_new = cur.lastrowid, True
        add_note(conn, tid, "voice-agent", "Created from phone call. Priority reasoning: " + "; ".join(d.reasons))

    if d.action in ("transfer_to_human", "page_oncall", "callback_next_business_day"):
        conn.execute("INSERT INTO escalations(ticket_id,kind,reason,created_at) VALUES (?,?,?,?)",
                     (tid, d.action, "; ".join(d.reasons), _iso(now)))
    conn.commit()
    return {"ticket": get(conn, tid), "decision": d, "is_new": is_new, "caller": caller,
            "incident": dict(incident) if incident else None}


def get(conn: sqlite3.Connection, ticket_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        return None
    t = dict(row)
    t["ref"] = ref(row["id"])
    t["notes"] = [dict(n) for n in conn.execute(
        "SELECT author, body, created_at FROM ticket_notes WHERE ticket_id=? ORDER BY id", (ticket_id,))]
    return t


def list_tickets(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    sql, args = "SELECT id FROM tickets", []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY priority, id"
    return [get(conn, r["id"]) for r in conn.execute(sql, args)]


def set_status(conn: sqlite3.Connection, ticket_id: int, status: str, assignee: str | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    resolved = _iso(_now()) if status in ("resolved", "closed") else None
    conn.execute(
        "UPDATE tickets SET status=?, assignee=COALESCE(?,assignee), updated_at=?, "
        "resolved_at=COALESCE(?,resolved_at) WHERE id=?",
        (status, assignee, _iso(_now()), resolved, ticket_id),
    )
    conn.commit()
