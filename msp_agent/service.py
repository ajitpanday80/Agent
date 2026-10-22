"""Call-level ticket service. One CallService per phone call.

Backends hold the tickets: ZohoBackend when Zoho is configured (Zoho is the system of record),
otherwise LocalBackend (SQLite mock). The local database always keeps a call log for audit, known incidents,
and tickets that could not be pushed to Zoho (retry with `cli retry-sync`).
"""
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import escalation, tickets, zoho

logger = logging.getLogger("msp-agent.service")

NEVER_SELF_RESOLVED = ("security", "access")


@dataclass
class Facts:
    caller_name: str
    company: str
    callback_number: str
    category: str
    summary: str
    scope: str = "single_user"
    severity: str = "degraded"
    details: str = ""
    workaround_available: bool = False
    security_concern: bool = False
    data_loss: bool = False
    caller_requests_human: bool = False
    caller_hostile: bool = False


@dataclass
class Outcome:
    ref: str
    backend_id: str
    decision: escalation.Decision
    is_new: bool
    caller: dict
    priority: int
    category: str
    incident: dict | None = None
    url: str | None = None
    fallback: bool = False


def _vip_emails() -> set[str]:
    return {e.strip().lower() for e in os.getenv("MSP_VIP_EMAILS", "").split(",") if e.strip()}


def decide_for(f: Facts, caller: dict, prior: int, now: datetime | None = None) -> escalation.Decision:
    return escalation.decide(
        category=f.category, scope=f.scope, severity=f.severity, workaround_available=f.workaround_available,
        security_concern=f.security_concern, data_loss=f.data_loss, caller_requests_human=f.caller_requests_human,
        caller_hostile=f.caller_hostile, is_vip=caller.get("is_vip", False),
        caller_recognized=caller["recognized"], prior_contacts=prior, now=now)


# ------------------------------------------------------------------------------------------ local backend
class LocalBackend:
    name = "local"

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def identify(self, name: str, company: str, phone: str) -> dict:
        return tickets.identify_caller(self.conn, name, company, phone)

    def open(self, f: Facts) -> Outcome:
        r = tickets.create(
            self.conn, caller_name=f.caller_name, company_name=f.company, callback_number=f.callback_number,
            category=f.category, summary=f.summary, details=f.details, scope=f.scope, severity=f.severity,
            workaround_available=f.workaround_available, security_concern=f.security_concern,
            data_loss=f.data_loss, caller_requests_human=f.caller_requests_human, caller_hostile=f.caller_hostile)
        t = r["ticket"]
        return Outcome(ref=t["ref"], backend_id=str(t["id"]), decision=r["decision"], is_new=r["is_new"],
                       caller=r["caller"], priority=t["priority"], category=f.category, incident=r["incident"])

    def add_note(self, backend_id: str, text: str) -> None:
        tickets.add_note(self.conn, int(backend_id), "voice-agent", text)

    def close(self, backend_id: str, resolution: str) -> None:
        tickets.add_note(self.conn, int(backend_id), "voice-agent", f"Resolved on the call. Fix given: {resolution}")
        tickets.set_status(self.conn, int(backend_id), "closed")

    def status_for(self, ref: str, caller: dict) -> str | None:
        tid = tickets.parse_ref(ref)
        t = tickets.get(self.conn, tid) if tid else None
        if not t or not caller["recognized"] or t["company_id"] != caller["company_id"]:
            return None
        return (f"Ticket {t['ref']}: {t['summary']}. Status {t['status'].replace('_', ' ')}, priority P{t['priority']}, "
                f"assigned to {t['assignee'] or 'no one yet'}.")


# -------------------------------------------------------------------------------------------- zoho backend
class ZohoBackend:
    name = "zoho"

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn   # local DB: call log (repeat counting) and known incidents

    def identify(self, name: str, company: str, phone: str) -> dict:
        c = zoho.find_contact(name, company, phone)
        if not c:
            return {"recognized": False, "contact_id": None, "company_id": None, "company_name": company,
                    "name": name, "is_vip": False, "phone_matches_record": False}
        acct = c.get("account") or {}
        full = f"{c.get('firstName') or ''} {c.get('lastName') or ''}".strip()
        return {"recognized": True, "contact_id": c["id"], "company_id": acct.get("id"),
                "company_name": acct.get("accountName", company), "name": full,
                "is_vip": (c.get("email") or "").lower() in _vip_emails(),
                "phone_matches_record": c["_phone_match"]}

    def _find_open(self, caller: dict, category: str) -> dict | None:
        # Only merge repeat calls for recognized callers; never merge an unverified caller into anyone's ticket.
        if not caller["recognized"]:
            return None
        for t in zoho.open_tickets():
            parsed = zoho.parse_subject(t.get("subject", ""))
            if parsed and parsed[1] == category and (t.get("contact") or {}).get("id") == caller["contact_id"]:
                return {**t, "priority_n": parsed[0]}
        return None

    def _prior(self, ticket_id: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM call_log WHERE backend='zoho' AND ticket_id=?",
                                 (str(ticket_id),)).fetchone()[0]

    def open(self, f: Facts) -> Outcome:
        caller = self.identify(f.caller_name, f.company, f.callback_number)
        dup = self._find_open(caller, f.category)
        d = decide_for(f, caller, prior=self._prior(dup["id"]) if dup else 0)
        incident = next(iter(tickets.active_incidents(self.conn, f.category)), None)
        incident = dict(incident) if incident else None

        if dup:
            zoho.add_comment(dup["id"], "Repeat call logged by the voice agent.\n" + _describe(f, caller, d, incident))
            new_p = min(dup["priority_n"], d.priority)
            if new_p != dup["priority_n"]:
                zoho.update_ticket(dup["id"], {"priority": zoho.PRIORITY_MAP[new_p],
                                               "subject": zoho.make_subject(new_p, f.category, f.summary)})
            return Outcome(ref=str(dup["ticketNumber"]), backend_id=str(dup["id"]), decision=d, is_new=False,
                           caller=caller, priority=new_p, category=f.category, incident=incident)

        payload = {
            "subject": zoho.make_subject(d.priority, f.category, f.summary),
            "departmentId": zoho._department_id(),
            "description": _describe(f, caller, d, incident),
            "priority": zoho.PRIORITY_MAP[d.priority],
            "channel": "Phone", "status": "Open",
        }
        if caller["recognized"]:
            payload["contactId"] = caller["contact_id"]
        else:   # one shared placeholder contact, so strangers never become 'recognized' contacts later
            payload["contact"] = {"lastName": "Unverified Caller", "email": zoho.UNVERIFIED_EMAIL}
        status, resp = zoho.api("POST", "/tickets", payload)
        if status not in (200, 201) or "id" not in resp:
            raise RuntimeError(f"Zoho create failed {status}: {resp}")
        return Outcome(ref=str(resp.get("ticketNumber") or resp["id"]), backend_id=str(resp["id"]), decision=d,
                       is_new=True, caller=caller, priority=d.priority, category=f.category, incident=incident,
                       url=resp.get("webUrl"))

    def add_note(self, backend_id: str, text: str) -> None:
        zoho.add_comment(backend_id, text)

    def close(self, backend_id: str, resolution: str) -> None:
        zoho.add_comment(backend_id, f"Resolved on the call. Fix given: {resolution}")
        zoho.update_ticket(backend_id, {"status": "Closed"})

    def status_for(self, ref: str, caller: dict) -> str | None:
        if not caller["recognized"]:
            return None
        t = zoho.find_ticket_by_number(re.sub(r"\D", "", ref))
        if not t:
            return None
        c = t.get("contact") or {}
        same = c.get("id") == caller["contact_id"] or (
            caller["company_id"] and (c.get("account") or {}).get("id") == caller["company_id"])
        if not same:
            return None
        parsed = zoho.parse_subject(t.get("subject", ""))
        return (f"Ticket {t['ticketNumber']}: {t.get('subject', '')}. Status {t.get('status')}"
                f"{f', priority P{parsed[0]}' if parsed else ''}.")


def _describe(f: Facts, caller: dict, d: escalation.Decision, incident: dict | None) -> str:
    who = (f"Caller (recognized in Zoho contacts, NOT identity-verified): {caller['name']} of {caller['company_name']}"
           if caller["recognized"] else
           f"Caller (UNRECOGNIZED, share nothing, verify before acting): {f.caller_name}, company said: {f.company}")
    lines = [
        "Created by the AI voice agent from a phone call.", who, f"Callback number: {f.callback_number}",
        f"Category: {f.category}. Priority P{d.priority}. Action: {d.action}",
        f"Priority reasoning: {'; '.join(d.reasons)}",
    ]
    if caller["recognized"] and not caller["phone_matches_record"]:
        lines.append("Note: the callback number does not match the number on file.")
    if d.requires_verification:
        lines.append("ACCESS REQUEST: do not change anything until a technician verifies the caller's identity.")
    if d.security:
        lines.append("SECURITY INCIDENT: caller was told to disconnect from the network but not power off.")
    if incident:
        lines.append(f"Related active incident: {incident['title']}")
    if f.details:
        lines.append(f"Details: {f.details}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------- service
class CallService:
    def __init__(self, conn: sqlite3.Connection, backend=None):
        self.conn = conn
        self.local = LocalBackend(conn)
        self.backend = backend or (ZohoBackend(conn) if zoho.enabled() else self.local)
        self.caller: dict | None = None
        self.given: dict = {}          # what the caller told us, even if unrecognized
        self.outcome: Outcome | None = None
        self.closed = False
        self.heard: list[str] = []     # last things the caller said, for the dropped-call record

    # -- logging
    def _log(self, outcome: str, o: Outcome | None = None, **extra) -> None:
        o = o or self.outcome
        self.conn.execute(
            "INSERT INTO call_log(created_at,backend,ticket_id,ticket_ref,caller_name,company,category,priority,"
            "action,outcome,reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "local" if (o and o.fallback) else self.backend.name, o.backend_id if o else None,
             o.ref if o else None, self.given.get("name"), self.given.get("company"), o.category if o else None,
             o.priority if o else None, o.decision.action if o else None, outcome,
             "; ".join(o.decision.reasons) if o else extra.get("note")))
        self.conn.commit()

    # -- caller
    def identify(self, name: str, company: str, phone: str = "") -> dict:
        self.given = {"name": name, "company": company, "phone": phone}
        try:
            self.caller = self.backend.identify(name, company, phone)
        except Exception as e:  # noqa: BLE001 - fail closed: unknown directory means unrecognized
            logger.error("caller lookup failed, treating as unrecognized: %s", e)
            self.caller = {"recognized": False, "contact_id": None, "company_id": None, "company_name": company,
                           "name": name, "is_vip": False, "phone_matches_record": False}
        return self.caller

    # -- tickets
    def open_ticket(self, f: Facts) -> Outcome:
        self.given = {"name": f.caller_name, "company": f.company, "phone": f.callback_number}
        try:
            o = self.backend.open(f)
        except Exception as e:  # noqa: BLE001 - never lose a ticket because Zoho is down
            if self.backend is self.local:
                raise
            logger.error("Zoho open failed, saving locally for retry: %s", e)
            o = self.local.open(f)
            o.fallback = True
            self.conn.execute("UPDATE tickets SET sync_error=? WHERE id=?", (f"pending: {e}"[:500], int(o.backend_id)))
            self.conn.commit()
        self.outcome, self.closed = o, False
        self._log("created" if o.is_new else "repeat", o)
        return o

    def close_ticket(self, resolution: str) -> str:
        o = self.outcome
        if not o:
            return "No ticket has been opened on this call. Open one first."
        if o.category in NEVER_SELF_RESOLVED or o.decision.security or o.decision.requires_verification:
            return "This ticket needs a technician and cannot be closed by you. Leave it open."
        if o.decision.action != "ticket":
            return "This ticket has been escalated to a human and cannot be closed by you. Leave it open."
        backend = self.local if o.fallback else self.backend
        backend.close(o.backend_id, resolution)
        self.closed = True
        self._log("closed_resolved", o)
        return f"Ticket {o.ref} closed as resolved."

    def add_note(self, ref: str, text: str) -> str:
        if not self.caller or not self.caller["recognized"]:
            return "Caller is not recognized. Do not modify tickets."
        o = self.outcome
        if o and (ref.strip() in ("", o.ref) or re.sub(r"\D", "", ref) == re.sub(r"\D", "", o.ref)):
            (self.local if o.fallback else self.backend).add_note(o.backend_id, text)
            return f"Note added to {o.ref}."
        return "You can only add notes to the ticket opened on this call."

    def status(self, ref: str) -> str:
        if not self.caller or not self.caller["recognized"]:
            return "Caller is not recognized. Do not share ticket details. Offer to take a message in a new ticket."
        try:
            s = self.backend.status_for(ref, self.caller)
        except Exception as e:  # noqa: BLE001
            logger.error("status lookup failed: %s", e)
            return "Could not look that up right now. Offer to create a ticket so a technician follows up."
        return s or "No matching ticket found for this caller's company."

    # -- end of call
    def end_call(self) -> None:
        """Called when the call ends for any reason (hang-up included). Idempotent."""
        if getattr(self, "_ended", False):
            return
        self._ended = True
        try:
            if self.outcome and not self.closed:
                backend = self.local if self.outcome.fallback else self.backend
                backend.add_note(self.outcome.backend_id, "Call ended. Ticket remains open for a technician.")
            elif not self.outcome and self.given.get("name") and self.given.get("phone"):
                said = " | ".join(self.heard[-6:])
                f = Facts(caller_name=self.given["name"], company=self.given.get("company", ""),
                          callback_number=self.given["phone"], category="other", scope="single_user",
                          severity="minor", summary="Call ended before the issue was captured",
                          details=f"Caller disconnected before a ticket was opened. Last things heard: {said or 'nothing'}")
                self.open_ticket(f)
                self._log("dropped_call_ticket")
            elif not self.outcome:
                self._log("dropped_call", note="caller disconnected before giving name and number")
        except Exception as e:  # noqa: BLE001 - end-of-call must never raise
            logger.error("end_call failed: %s", e)
