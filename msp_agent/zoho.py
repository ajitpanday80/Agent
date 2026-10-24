"""Zoho Desk API client. Zoho Desk is the authoritative ticket system.

Auth: Zoho OAuth "Self Client" refresh token -> short-lived access token (cached, refreshed on expiry/401).
API docs: https://desk.zoho.com/DeskAPIDocument  (use your data center's domain, e.g. desk.zoho.in)
"""
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# Zoho's default priority picklist here is High / Medium / Low. Edit if you customized it.
PRIORITY_MAP = {1: "High", 2: "High", 3: "Medium", 4: "Low"}
ZOHO_STATUS = {"open": "Open", "in_progress": "Open", "waiting_on_customer": "On Hold", "resolved": "Closed",
               "closed": "Closed"}
_token = {"value": None, "expires": 0.0}
logger = logging.getLogger(__name__)


def _cfg(name: str) -> str:
    return os.getenv(name, "").strip()


def enabled() -> bool:
    need = ("ZOHO_ACCOUNTS_URL", "ZOHO_DESK_API_URL", "ZOHO_DESK_ORG_ID", "ZOHO_CLIENT_ID",
            "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN")
    return all(_cfg(n) for n in need)


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


def request(method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
    """Perform a Zoho operation and raise a useful error for staff or voice workflow handling."""
    if not enabled():
        raise RuntimeError("Zoho Desk is not configured. Set the ZOHO_* values before serving tickets.")
    status, resp = api(method, path, body=body, params=params)
    if status < 200 or status >= 300:
        raise RuntimeError(f"Zoho Desk {method} {path} failed ({status}): {resp}")
    return resp


def list_tickets(*, offset: int = 1, limit: int = 50) -> list[dict]:
    resp = request("GET", "/tickets", params={"from": max(offset, 1), "limit": min(max(limit, 1), 50),
                                                "sortBy": "-modifiedTime", "include": "assignee"})
    return resp.get("data", [])


def list_agents(*, offset: int = 0, limit: int = 200, department_id: str = "") -> list[dict]:
    params = {"from": offset, "limit": min(max(limit, 1), 200), "status": "ACTIVE"}
    if department_id:
        params["departmentIds"] = department_id
    return request("GET", "/agents", params=params).get("data", [])


def list_all_agents(*, department_id: str = "") -> list[dict]:
    """Page through the full active-agent directory (Zoho supports up to 200 agents per request)."""
    agents = []
    for offset in range(0, 100_000, 200):
        batch = list_agents(offset=offset, limit=200, department_id=department_id)
        agents.extend(batch)
        if len(batch) < 200:
            break
    return agents


def least_loaded_agent(department_id: str = "") -> dict | None:
    """Return an active department agent with the fewest active tickets assigned in Zoho."""
    try:
        agents = [a for a in list_all_agents(department_id=department_id)
                  if a.get("id") and a.get("isActive", True)]
    except Exception as error:
        logger.warning("Could not list Zoho agents for ticket assignment: %s", type(error).__name__)
        return None
    if not agents:
        return None
    if len(agents) == 1:
        return agents[0]

    workloads = {str(agent["id"]): 0 for agent in agents}
    statuses = ("Open", "On Hold", "Escalated")
    try:
        for agent in agents:
            agent_id = str(agent["id"])
            for status in statuses:
                result = request("GET", "/tickets/search", params={
                    "assignee": agent_id, "status": status, "from": 0, "limit": 1,
                    **({"departmentId": department_id} if department_id else {}),
                })
                workloads[agent_id] += int(result.get("count", len(result.get("data", []))))
    except Exception as error:
        # Keep ticket creation available when search scope/API access is unavailable. Count the
        # latest Zoho tickets as a fallback, and choose the first active agent if even that fails.
        logger.warning("Could not calculate Zoho agent workload: %s", type(error).__name__)
        workloads = {str(agent["id"]): 0 for agent in agents}
        try:
            for ticket in list_tickets():
                status = str(ticket.get("status") or ticket.get("statusType") or "").lower()
                if status in ("open", "on hold", "escalated"):
                    owner = ticket.get("assignee") or {}
                    owner_id = str(ticket.get("assigneeId") or owner.get("id") or "")
                    if owner_id in workloads:
                        workloads[owner_id] += 1
        except Exception as fallback_error:
            logger.warning("Zoho workload fallback failed: %s", type(fallback_error).__name__)
    return min(agents, key=lambda a: (workloads[str(a["id"])],
                                      str(a.get("name") or a.get("email") or a["id"]).lower()))


def list_contacts(*, offset: int = 0, limit: int = 50) -> list[dict]:
    return request("GET", "/contacts", params={"from": max(offset, 0), "limit": min(max(limit, 1), 50),
                                                  "include": "accounts"}).get("data", [])


def list_accounts(*, offset: int = 0, limit: int = 50) -> list[dict]:
    return request("GET", "/accounts", params={"from": max(offset, 0), "limit": min(max(limit, 1), 50)}).get("data", [])


def find_account_by_name(name: str) -> dict | None:
    """Resolve an exact account name across Zoho Desk pages without truncating at the first 50."""
    wanted = " ".join(name.lower().split())
    for offset in range(0, 100_000, 50):
        rows = list_accounts(offset=offset, limit=50)
        match = next((row for row in rows if " ".join(
            str(row.get("accountName") or row.get("name") or "").lower().split()) == wanted), None)
        if match or len(rows) < 50:
            return match
    return None


def create_contact(payload: dict) -> dict:
    return request("POST", "/contacts", payload)


def create_account(payload: dict) -> dict:
    return request("POST", "/accounts", payload)


def match_contact(name: str, company: str, phone: str) -> dict | None:
    phone_values = _phone_variants(phone)
    if not phone_values or not name.strip() or not company.strip():
        return None
    wanted = " ".join(name.lower().split())
    try:
        rows = request("GET", "/contacts/search", params={"searchStr": phone, "from": 0, "limit": 50}).get("data", [])
    except RuntimeError:
        rows = list_contacts()
    for row in rows:
        full = " ".join((row.get("firstName", "") + " " + row.get("lastName", "")).lower().split())
        acct = row.get("account") or {}
        row_values = _record_phone_variants(row)
        if full == wanted and phone_values & row_values and (acct.get("accountName", "").strip().lower() == company.strip().lower()):
            return row
    return None


def _phone_variants(phone: str) -> set[str]:
    """Compare punctuation-free numbers and the common US +1 / ten-digit forms."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        return {digits, digits[1:]}
    if len(digits) == 10:
        return {digits, "1" + digits}
    return {digits} if len(digits) >= 7 else set()


def _record_phone_variants(row: dict) -> set[str]:
    return _phone_variants(row.get("phone", "")) | _phone_variants(row.get("mobile", ""))


def lookup_caller_by_phone(phone: str) -> dict:
    """Find a unique Zoho contact/account by caller ID. Caller ID is not identity verification."""
    variants = _phone_variants(phone)
    if not variants:
        return {"contact": None, "account": None}

    try:
        contact_rows = request("GET", "/contacts/search", params={
            "searchStr": phone, "from": 0, "limit": 50, "include": "accounts",
        }).get("data", [])
    except RuntimeError:
        contact_rows = list_contacts(offset=0, limit=50)
    contacts = [row for row in contact_rows if variants & _record_phone_variants(row)]
    unique_contacts = {str(row.get("id")): row for row in contacts if row.get("id")}
    contact = next(iter(unique_contacts.values())) if len(unique_contacts) == 1 else None

    if contact and contact.get("account"):
        return {"contact": contact, "account": contact["account"]}
    if contacts:
        related = {str((row.get("account") or {}).get("id")): row.get("account")
                   for row in contacts if (row.get("account") or {}).get("id")}
        if len(related) == 1:
            return {"contact": contact, "account": next(iter(related.values()))}

    try:
        account_rows = request("GET", "/accounts/search", params={
            "searchStr": phone, "from": 0, "limit": 50,
        }).get("data", [])
    except RuntimeError:
        account_rows = list_accounts(offset=0, limit=50)
    accounts = [row for row in account_rows if variants & _record_phone_variants(row)]
    unique_accounts = {str(row.get("id")): row for row in accounts if row.get("id")}
    account = next(iter(unique_accounts.values())) if len(unique_accounts) == 1 else None

    return {"contact": contact, "account": account}


def get_ticket(ticket_id: str) -> dict:
    return request("GET", f"/tickets/{urllib.parse.quote(str(ticket_id), safe='')}")


def create_ticket(payload: dict) -> dict:
    return request("POST", "/tickets", payload)


def update_ticket(ticket_id: str, payload: dict) -> dict:
    return request("PATCH", f"/tickets/{urllib.parse.quote(str(ticket_id), safe='')}", payload)


def add_comment(ticket_id: str, content: str, *, public: bool = False) -> dict:
    return request("POST", f"/tickets/{urllib.parse.quote(str(ticket_id), safe='')}/comments",
                   {"content": content, "isPublic": public})


def list_comments(ticket_id: str, *, offset: int = 1, limit: int = 25) -> list[dict]:
    return request("GET", f"/tickets/{urllib.parse.quote(str(ticket_id), safe='')}/comments",
                   params={"from": max(offset, 1), "limit": min(max(limit, 1), 50)}).get("data", [])


def _department_id() -> str:
    if _cfg("ZOHO_DESK_DEPARTMENT_ID"):
        return _cfg("ZOHO_DESK_DEPARTMENT_ID")
    status, resp = api("GET", "/departments")
    deps = [d for d in resp.get("data", []) if d.get("isEnabled")]
    if status != 200 or not deps:
        raise RuntimeError(f"could not find a Zoho department: {resp}")
    return next((d for d in deps if d.get("isDefault")), deps[0])["id"]


def _contact(t: dict, email: str | None) -> dict:
    parts = t["caller_name"].strip().split(None, 1) or ["Unknown"]
    first, last = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
    c = {"lastName": last, "phone": t["callback_number"]}
    if first:
        c["firstName"] = first
    if email:
        c["email"] = email
    return c


def _description(t: dict, decision) -> str:
    lines = [
        f"Created through MSP Helpdesk. Intake reference: {t['ref']}",
        f"Caller: {t['caller_name']} ({t['company_name']}), callback {t['callback_number']}",
        f"Caller recognized in records: {'yes' if t['caller_recognized'] else 'NO (unrecognized caller)'}. "
        "Recognized is not identity-verified.",
        f"Category: {t['category']}. Priority P{t['priority']}. Action: {decision.action}",
        f"Priority reasoning: {'; '.join(decision.reasons)}",
        f"Target first response: {decision.ack_minutes} minutes. Target resolution: {decision.resolve_hours} hours.",
        f"Target first response: {decision.ack_minutes} minutes. Target resolution: {decision.resolve_hours} hours.",
    ]
    if decision.requires_verification:
        lines.append("ACCESS REQUEST: do not change anything until a technician verifies the caller's identity.")
    if decision.security:
        lines.append("SECURITY INCIDENT: caller was told to disconnect from the network but not power off.")
    if t["details"]:
        lines.append(f"Details: {t['details']}")
    return "\n".join(lines)
