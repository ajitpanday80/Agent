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
            "subject": f"[P{t['priority']}] {t['summary']}",
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
