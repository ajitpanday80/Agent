"""Push tickets to Zoho Desk (one-way). The local DB and escalation rules stay the source of truth.

Auth: Zoho OAuth "Self Client" refresh token -> short-lived access token (cached, refreshed on expiry/401).
API docs: https://desk.zoho.com/DeskAPIDocument  (use your data center's domain, e.g. desk.zoho.in)
Sync must never break a live call: every public function returns a result instead of raising.
"""
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("msp-agent.zoho")

# Zoho's default priority picklist here is High / Medium / Low. Edit if you customized it.
PRIORITY_MAP = {1: "High", 2: "High", 3: "Medium", 4: "Low"}
ZOHO_STATUS = {"open": "Open", "in_progress": "Open", "waiting_on_customer": "On Hold", "resolved": "Closed",
               "closed": "Closed"}
_token = {"value": None, "expires": 0.0}


def _cfg(name: str) -> str:
    return os.getenv(name, "").strip()


def enabled() -> bool:
    need = ("ZOHO_ACCOUNTS_URL", "ZOHO_DESK_API_URL", "ZOHO_DESK_ORG_ID", "ZOHO_CLIENT_ID",
            "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN")
    return all(_cfg(n) for n in need) and _cfg("ZOHO_SYNC").lower() != "false"


def _http(method: str, url: str, headers: dict, body: dict | None = None, form: dict | None = None):
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    elif body is not None:
        data = json.dumps(body).encode()
        headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"message": raw[:200].decode(errors="replace")}


def _access_token(force: bool = False) -> str:
    if not force and _token["value"] and time.time() < _token["expires"]:
        return _token["value"]
    status, body = _http("POST", _cfg("ZOHO_ACCOUNTS_URL") + "/oauth/v2/token", {}, form={
        "grant_type": "refresh_token", "client_id": _cfg("ZOHO_CLIENT_ID"),
        "client_secret": _cfg("ZOHO_CLIENT_SECRET"), "refresh_token": _cfg("ZOHO_REFRESH_TOKEN"),
    })
    if status != 200 or "access_token" not in body:
        raise RuntimeError(f"Zoho token refresh failed: {body.get('error') or body}")
    _token["value"] = body["access_token"]
    _token["expires"] = time.time() + int(body.get("expires_in", 3600)) - 120
    return _token["value"]


def api(method: str, path: str, body: dict | None = None, params: dict | None = None, org: bool = True):
    url = _cfg("ZOHO_DESK_API_URL") + path + (("?" + urllib.parse.urlencode(params)) if params else "")
    for attempt in (1, 2):
        headers = {"Authorization": f"Zoho-oauthtoken {_access_token(force=attempt == 2)}"}
        if org:
            headers["orgId"] = _cfg("ZOHO_DESK_ORG_ID")
        status, resp = _http(method, url, headers, body=body)
        if status != 401 or attempt == 2:
            return status, resp
    return status, resp


def _department_id() -> str:
    if _cfg("ZOHO_DESK_DEPARTMENT_ID"):
        return _cfg("ZOHO_DESK_DEPARTMENT_ID")
    status, resp = api("GET", "/departments")
    deps = [d for d in resp.get("data", []) if d.get("isEnabled")]
    if status != 200 or not deps:
        raise RuntimeError(f"could not find a Zoho department: {resp}")
    return next((d for d in deps if d.get("isDefault")), deps[0])["id"]


def _contact(t: dict, email: str | None) -> dict:
    parts = t["caller_name"].strip().split(None, 1)
    first, last = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
    c = {"lastName": last, "phone": t["callback_number"]}
    if first:
        c["firstName"] = first
    if email:
        c["email"] = email
    return c


def _description(t: dict, decision) -> str:
    lines = [
        f"Created by the AI voice agent from a phone call. Local reference: {t['ref']}",
        f"Caller: {t['caller_name']} ({t['company_name']}), callback {t['callback_number']}",
        f"Caller recognized in records: {'yes' if t['caller_recognized'] else 'NO (unrecognized caller)'}. "
        "Recognized is not identity-verified.",
        f"Category: {t['category']}. Priority P{t['priority']}. Action: {decision.action}",
        f"Priority reasoning: {'; '.join(decision.reasons)}",
    ]
    if decision.requires_verification:
        lines.append("ACCESS REQUEST: do not change anything until a technician verifies the caller's identity.")
    if decision.security:
        lines.append("SECURITY INCIDENT: caller was told to disconnect from the network but not power off.")
    if t["details"]:
        lines.append(f"Details: {t['details']}")
    return "\n".join(lines)


def sync_ticket(conn: sqlite3.Connection, result: dict) -> dict:
    """Push the outcome of tickets.create() to Zoho. Returns {'ok': bool, ...}; never raises."""
    if not enabled():
        return {"ok": False, "skipped": True}
    t, d = result["ticket"], result["decision"]
    try:
        if not result["is_new"] and t.get("zoho_id"):
            status, resp = api("POST", f"/tickets/{t['zoho_id']}/comments", {
                "content": "Repeat call logged by the voice agent.\n" + _description(t, d), "isPublic": False})
            if status not in (200, 201):
                raise RuntimeError(f"comment failed {status}: {resp}")
            prev = result.get("previous_priority")
            if prev is not None and prev != t["priority"]:
                api("PATCH", f"/tickets/{t['zoho_id']}", {"priority": PRIORITY_MAP[t["priority"]]})
            return {"ok": True, "zoho_id": t["zoho_id"], "updated": True}

        email = _email_on_file(conn, t)
        status, resp = api("POST", "/tickets", {
            "subject": make_subject(t["priority"], t["category"], t["summary"]),
            "departmentId": _department_id(),
            "contact": _contact(t, email),
            "description": _description(t, d),
            "priority": PRIORITY_MAP[t["priority"]],
            "channel": "Phone",
            "status": ZOHO_STATUS.get(t["status"], "Open"),
        })
        if status not in (200, 201) or "id" not in resp:
            raise RuntimeError(f"create failed {status}: {resp}")
        conn.execute("UPDATE tickets SET zoho_id=?, zoho_number=?, zoho_url=?, sync_error=NULL WHERE id=?",
                     (resp["id"], resp.get("ticketNumber"), resp.get("webUrl"), t["id"]))
        conn.commit()
        return {"ok": True, "zoho_id": resp["id"], "number": resp.get("ticketNumber"), "url": resp.get("webUrl")}
    except Exception as e:  # noqa: BLE001 - a Zoho outage must not break the call
        logger.error("Zoho sync failed for %s: %s", t["ref"], e)
        conn.execute("UPDATE tickets SET sync_error=? WHERE id=?", (str(e)[:500], t["id"]))
        conn.commit()
        return {"ok": False, "error": str(e)}


def _email_on_file(conn: sqlite3.Connection, t: dict) -> str | None:
    if not t.get("contact_id"):
        return None
    row = conn.execute("SELECT email FROM contacts WHERE id=?", (t["contact_id"],)).fetchone()
    return row["email"] if row else None


# ---------------------------------------------------------------------------------------------------------
# Zoho as the system of record: directory, ticket lookup and updates.
# Search endpoints need the Desk.search.READ scope, which the first token did not have, so lookups list and
# filter client-side (fine for a pilot-sized directory). Directory is cached briefly to keep calls fast.
# ---------------------------------------------------------------------------------------------------------
import re  # noqa: E402

UNVERIFIED_EMAIL = "unverified-caller@voice-agent.invalid"   # shared placeholder contact: never matched as a caller
SUBJECT_RE = re.compile(r"^\[P(\d)\]\s*\[(\w+)\]")
_dir = {"at": 0.0, "rows": []}


def make_subject(priority: int, category: str, summary: str) -> str:
    return f"[P{priority}] [{category}] {summary}"


def parse_subject(subject: str) -> tuple[int, str] | None:
    m = SUBJECT_RE.match(subject or "")
    return (int(m.group(1)), m.group(2)) if m else None


def _list_all(path: str, params: dict, cap: int = 1000) -> list[dict]:
    rows, frm = [], 1
    while len(rows) < cap:
        status, resp = api("GET", path, params={**params, "from": frm, "limit": 100})
        if status == 204:
            break
        if status != 200:
            raise RuntimeError(f"Zoho list {path} failed {status}: {resp}")
        data = resp.get("data", [])
        rows += data
        if len(data) < 100:
            break
        frm += 100
    return rows


def directory(ttl: int = 300, force: bool = False) -> list[dict]:
    """All real contacts with their company, cached for ttl seconds."""
    if not force and _dir["rows"] and time.time() - _dir["at"] < ttl:
        return _dir["rows"]
    rows = _list_all("/contacts", {"include": "accounts"}, cap=2000)
    _dir["rows"] = [r for r in rows if (r.get("email") or "").lower() != UNVERIFIED_EMAIL]
    _dir["at"] = time.time()
    return _dir["rows"]


def _digits(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def find_contact(name: str, company: str, phone: str = "") -> dict | None:
    """Recognized means name AND company both match a contact. Phone is reported separately."""
    tokens = [t for t in name.lower().split() if t]
    company = company.strip().lower()
    if not tokens or not company:
        return None
    for c in directory():
        full = f"{c.get('firstName') or ''} {c.get('lastName') or ''}".lower()
        acct = ((c.get("account") or {}).get("accountName") or "").lower()
        if all(t in full for t in tokens) and acct and (company in acct or acct in company):
            nums = {_digits(c.get("phone")), _digits(c.get("mobile"))} - {""}
            return {**c, "_phone_match": bool(phone) and _digits(phone) in nums}
    return None


def open_tickets() -> list[dict]:
    out: list[dict] = []
    for st in ("Open", "On Hold", "Escalated"):
        out += _list_all("/tickets", {"status": st, "include": "contacts",
                                      "fields": "ticketNumber,subject,status,priority"}, cap=500)
    return out


def find_ticket_by_number(number: str) -> dict | None:
    number = str(number).strip()
    for t in _list_all("/tickets", {"sortBy": "-createdTime", "include": "contacts",
                                    "fields": "ticketNumber,subject,status,priority"}, cap=500):
        if str(t.get("ticketNumber")) == number:
            return t
    return None


def add_comment(ticket_id: str, text: str) -> None:
    status, resp = api("POST", f"/tickets/{ticket_id}/comments", {"content": text, "isPublic": False})
    if status not in (200, 201):
        raise RuntimeError(f"comment failed {status}: {resp}")


def update_ticket(ticket_id: str, fields: dict) -> None:
    status, resp = api("PATCH", f"/tickets/{ticket_id}", fields)
    if status != 200:
        raise RuntimeError(f"update failed {status}: {resp}")


def push_pending(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Push tickets that were saved locally while Zoho was down. Returns [(local ref, result)]."""
    out = []
    rows = conn.execute("SELECT * FROM tickets WHERE zoho_id IS NULL AND sync_error LIKE 'pending:%'").fetchall()
    for r in rows:
        ref = f"T-{1000 + r['id']}"
        try:
            notes = conn.execute("SELECT author, body FROM ticket_notes WHERE ticket_id=? ORDER BY id",
                                 (r["id"],)).fetchall()
            payload = {
                "subject": make_subject(r["priority"], r["category"], r["summary"]),
                "departmentId": _department_id(),
                "description": "Saved locally while Zoho was unreachable.\n"
                               f"Caller: {r['caller_name']} ({r['company_name']}), callback {r['callback_number']}\n"
                               + "\n".join(f"[{n['author']}] {n['body']}" for n in notes),
                "priority": PRIORITY_MAP[r["priority"]], "channel": "Phone",
                "status": ZOHO_STATUS.get(r["status"], "Open"),
            }
            c = find_contact(r["caller_name"], r["company_name"], r["callback_number"])
            payload["contactId" if c else "contact"] = c["id"] if c else {
                "lastName": "Unverified Caller", "email": UNVERIFIED_EMAIL}
            status, resp = api("POST", "/tickets", payload)
            if status not in (200, 201) or "id" not in resp:
                raise RuntimeError(f"{status}: {resp}")
            conn.execute("UPDATE tickets SET zoho_id=?, zoho_number=?, zoho_url=?, sync_error=NULL WHERE id=?",
                         (resp["id"], resp.get("ticketNumber"), resp.get("webUrl"), r["id"]))
            conn.commit()
            out.append((ref, f"pushed as Zoho #{resp.get('ticketNumber')}"))
        except Exception as e:  # noqa: BLE001
            out.append((ref, f"FAILED: {e}"))
    return out
