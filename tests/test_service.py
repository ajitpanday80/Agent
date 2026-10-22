"""CallService against an in-memory fake Zoho (never the real one)."""
import pytest

from msp_agent import db, service, tickets, zoho
from msp_agent.service import Facts

CONTACTS = [
    {"id": "c1", "firstName": "Tom", "lastName": "Becker", "email": "tom@acme.example", "phone": "+14155550102",
     "mobile": None, "account": {"id": "A1", "accountName": "Acme Dental"}},
    {"id": "c2", "firstName": "Priya", "lastName": "Shah", "email": "priya@acme.example", "phone": "+14155550101",
     "mobile": None, "account": {"id": "A1", "accountName": "Acme Dental"}},
    {"id": "c3", "firstName": "Maria", "lastName": "Lopez", "email": "maria@nw.example", "phone": "+12125550111",
     "mobile": None, "account": {"id": "A2", "accountName": "Northwind Logistics"}},
    {"id": "c9", "firstName": None, "lastName": "Unverified Caller", "email": zoho.UNVERIFIED_EMAIL, "phone": None,
     "mobile": None, "account": None},
]


class FakeZoho:
    def __init__(self):
        self.tickets, self.calls, self.down = [], [], False
        self.by_id = {c["id"]: c for c in CONTACTS}

    def api(self, method, path, body=None, params=None, org=True):
        self.calls.append((method, path, body))
        if self.down:
            return 503, {"message": "down"}
        if method == "GET" and path == "/contacts":
            return 200, {"data": CONTACTS}
        if method == "GET" and path == "/tickets":
            rows = [t for t in self.tickets if t["status"] == (params or {}).get("status", t["status"])]
            return (200, {"data": rows}) if rows else (204, {})
        if method == "POST" and path == "/tickets":
            c = self.by_id.get(body.get("contactId")) or {"id": "c9", "lastName": "Unverified Caller", "account": None}
            t = {"id": f"z{len(self.tickets) + 1}", "ticketNumber": str(101 + len(self.tickets)),
                 "subject": body["subject"], "status": body["status"], "priority": body["priority"],
                 "contact": c, "webUrl": "http://z/t", "_payload": body, "_comments": []}
            self.tickets.append(t)
            return 201, t
        if method == "POST" and path.endswith("/comments"):
            self._t(path)["_comments"].append(body["content"])
            return 201, {}
        if method == "PATCH":
            self._t(path).update(body)
            return 200, {}
        return 404, {"message": f"unhandled {method} {path}"}

    def _t(self, path):
        tid = path.split("/")[2]
        return next(t for t in self.tickets if t["id"] == tid)


@pytest.fixture
def fz(monkeypatch, tmp_path):
    for k in ("ZOHO_ACCOUNTS_URL", "ZOHO_DESK_API_URL", "ZOHO_DESK_ORG_ID", "ZOHO_CLIENT_ID",
              "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN", "ZOHO_DESK_DEPARTMENT_ID"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("ZOHO_SYNC")
    monkeypatch.setenv("MSP_VIP_EMAILS", "priya@acme.example")
    monkeypatch.setenv("MSP_HOURS", "00:00-23:59")
    monkeypatch.setenv("MSP_TZ", "UTC")
    f = FakeZoho()
    monkeypatch.setattr(zoho, "api", f.api)
    monkeypatch.setitem(zoho._dir, "rows", [])
    monkeypatch.setitem(zoho._dir, "at", 0.0)
    f.conn = db.connect(tmp_path / "svc.db")
    return f


def svc(fz):
    return service.CallService(fz.conn)


def facts(**kw):
    base = dict(caller_name="Tom Becker", company="Acme Dental", callback_number="+14155550102",
                category="email", summary="Outlook will not open", scope="single_user", severity="degraded")
    base.update(kw)
    return Facts(**base)


def test_zoho_is_default_backend_when_configured(fz):
    assert svc(fz).backend.name == "zoho"


def test_recognized_caller_uses_real_contact(fz):
    s = svc(fz)
    c = s.identify("Tom Becker", "Acme Dental", "+14155550102")
    assert c["recognized"] and c["contact_id"] == "c1" and c["phone_matches_record"]
    o = s.open_ticket(facts())
    p = fz.tickets[0]["_payload"]
    assert p["contactId"] == "c1" and "contact" not in p
    assert p["subject"] == "[P4] [email] Outlook will not open" and p["priority"] == "Low" and p["channel"] == "Phone"
    assert o.ref == "101" and o.is_new


def test_name_alone_is_not_recognized(fz):
    s = svc(fz)
    assert not s.identify("Tom Becker", "Some Other Company")["recognized"]
    assert not s.identify("Tom Becker", "")["recognized"]


def test_unrecognized_gets_shared_contact_and_never_merges(fz):
    s1, s2 = svc(fz), svc(fz)
    for s in (s1, s2):
        s.identify("Zed Stranger", "Mystery Inc")
        s.open_ticket(facts(caller_name="Zed Stranger", company="Mystery Inc"))
    assert len(fz.tickets) == 2
    for t in fz.tickets:
        assert t["_payload"]["contact"]["email"] == zoho.UNVERIFIED_EMAIL and "contactId" not in t["_payload"]
        assert "UNRECOGNIZED" in t["_payload"]["description"]


def test_placeholder_contact_never_matches_a_caller(fz):
    assert not svc(fz).identify("Unverified Caller", "")["recognized"]
    assert zoho.find_contact("Unverified Caller", "anything") is None


def test_repeat_calls_merge_and_bump(fz):
    for _ in range(3):
        s = svc(fz)
        s.identify("Tom Becker", "Acme Dental")
        o = s.open_ticket(facts())
    assert len(fz.tickets) == 1 and not o.is_new
    assert o.priority == 3                      # P4 bumped after repeated contact
    assert fz.tickets[0]["subject"].startswith("[P3]") and len(fz.tickets[0]["_comments"]) == 2


def test_vip_bumped(fz):
    s = svc(fz)
    s.identify("Priya Shah", "Acme Dental")
    assert s.open_ticket(facts(caller_name="Priya Shah", severity="stopped")).priority == 2


def test_close_ticket_rules(fz):
    s = svc(fz)
    s.identify("Tom Becker", "Acme Dental")
    assert "No ticket" in s.close_ticket("restart")
    s.open_ticket(facts(category="performance", summary="slow"))
    assert "closed" in s.close_ticket("restart the PC").lower()
    t = fz.tickets[0]
    assert t["status"] == "Closed" and any("restart the PC" in c for c in t["_comments"])


@pytest.mark.parametrize("kw", [dict(category="access", summary="locked out", severity="stopped"),
                                dict(category="security", summary="ransomware", security_concern=True),
                                dict(category="email", severity="stopped", scope="whole_company")])
def test_close_refused_when_human_needed(fz, kw):
    s = svc(fz)
    s.identify("Tom Becker", "Acme Dental")
    s.open_ticket(facts(**kw))
    assert "cannot be closed" in s.close_ticket("x")
    assert fz.tickets[0]["status"] == "Open"


def test_zoho_down_falls_back_locally_then_retry_pushes(fz):
    fz.down = True
    s = svc(fz)
    s.identify("Tom Becker", "Acme Dental")           # lookup fails -> unrecognized, fail closed
    o = s.open_ticket(facts())
    assert o.fallback and fz.conn.execute("SELECT sync_error FROM tickets").fetchone()[0].startswith("pending")
    fz.down = False
    assert "pushed" in zoho.push_pending(fz.conn)[0][1] and len(fz.tickets) == 1
    assert zoho.push_pending(fz.conn) == []           # not pushed twice


def test_end_call_notes_open_ticket_and_is_idempotent(fz):
    s = svc(fz)
    s.identify("Tom Becker", "Acme Dental")
    s.open_ticket(facts())
    s.end_call()
    s.end_call()
    assert sum("Call ended" in c for c in fz.tickets[0]["_comments"]) == 1


def test_hangup_before_ticket_creates_dropped_call_ticket(fz):
    s = svc(fz)
    s.identify("Tom Becker", "Acme Dental", "+14155550102")
    s.heard += ["my outlook", "it just freezes"]
    s.end_call()
    assert len(fz.tickets) == 1
    assert "Call ended before the issue was captured" in fz.tickets[0]["subject"]
    assert "it just freezes" in fz.tickets[0]["_payload"]["description"]


def test_hangup_with_no_details_logs_only(fz):
    s = svc(fz)
    s.end_call()
    assert fz.tickets == [] and fz.conn.execute("SELECT outcome FROM call_log").fetchone()[0] == "dropped_call"


def test_status_only_for_own_company(fz):
    s = svc(fz)
    s.identify("Tom Becker", "Acme Dental")
    s.open_ticket(facts())
    other = svc(fz)
    other.identify("Maria Lopez", "Northwind Logistics")
    assert "Ticket 101" in s.status("101") and "No matching" in other.status("101")
    stranger = svc(fz)
    stranger.identify("Zed", "Nope")
    assert "not recognized" in stranger.status("101")


def test_call_log_written(fz):
    s = svc(fz)
    s.identify("Tom Becker", "Acme Dental")
    s.open_ticket(facts(category="performance"))
    s.close_ticket("restart")
    rows = [r[0] for r in fz.conn.execute("SELECT outcome FROM call_log ORDER BY id")]
    assert rows == ["created", "closed_resolved"]


def test_local_backend_flow_when_zoho_not_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOHO_SYNC", "false")
    conn = db.connect(tmp_path / "l.db")
    s = service.CallService(conn)
    assert s.backend.name == "local"
    assert s.identify("Tom Becker", "Acme Dental")["recognized"]
    o = s.open_ticket(facts(category="performance"))
    assert o.ref == "T-1001"
    s.close_ticket("restart")
    assert tickets.get(conn, 1)["status"] == "closed"
