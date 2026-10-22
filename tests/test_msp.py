from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from msp_agent import db, escalation, kb, tickets

BIZ = datetime(2026, 10, 21, 10, 0, tzinfo=ZoneInfo("UTC"))   # Wednesday 10:00
NIGHT = datetime(2026, 10, 21, 23, 0, tzinfo=ZoneInfo("UTC"))  # Wednesday 23:00
SAT = datetime(2026, 10, 24, 11, 0, tzinfo=ZoneInfo("UTC"))


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "t.db")


def mk(conn, **kw):
    base = dict(caller_name="Tom Becker", company_name="Acme Dental", callback_number="+14155550102",
                category="printing", summary="printer offline", scope="single_user", severity="degraded", now=BIZ)
    base.update(kw)
    return tickets.create(conn, **base)


def decide(**kw):
    base = dict(category="other", scope="single_user", severity="minor", now=BIZ)
    base.update(kw)
    return escalation.decide(**base)


def test_matrix_basics():
    assert decide(scope="whole_company", severity="stopped").priority == 1
    assert decide(scope="single_user", severity="minor").priority == 4
    assert decide(scope="single_user", severity="stopped").priority == 3
    assert decide(scope="several_users", severity="degraded").priority == 3


def test_workaround_lowers_urgency():
    assert decide(scope="whole_company", severity="stopped", workaround_available=True).priority == 2


def test_security_always_p1_and_human():
    d = decide(category="security", security_concern=True)
    assert d.priority == 1 and d.action == "transfer_to_human" and "disconnect" in d.caller_instructions
    assert "NOT" in d.caller_instructions  # do not power off


def test_security_after_hours_pages_oncall():
    assert decide(security_concern=True, now=NIGHT).action == "page_oncall"


def test_access_never_self_service():
    d = decide(category="access", severity="stopped")
    assert d.requires_verification and d.action == "ticket_callback_verify"


def test_vip_bump_and_repeat_bump():
    assert decide(severity="stopped", is_vip=True).priority == 2
    assert decide(severity="degraded", prior_contacts=2).priority == 3


def test_human_request_hours():
    assert decide(caller_requests_human=True, now=BIZ).action == "transfer_to_human"
    assert decide(caller_requests_human=True, now=NIGHT).action == "callback_next_business_day"
    assert decide(caller_requests_human=True, now=SAT).action == "callback_next_business_day"


def test_unknown_caller_access_flagged():
    d = decide(category="access", caller_recognized=False)
    assert d.requires_verification and any("not found" in r for r in d.reasons)


def test_ticket_create_and_sla(conn):
    r = mk(conn)
    t = r["ticket"]
    assert t["ref"] == "T-1001" and t["priority"] == 4 and r["caller"]["recognized"]
    assert t["ack_due"] > "2026-10-21T10:00:00Z"
    assert any("Priority reasoning" in n["body"] for n in t["notes"])


def test_vip_caller_from_records(conn):
    r = mk(conn, caller_name="Priya Shah", callback_number="+14155550101", severity="stopped", category="email")
    assert r["ticket"]["priority"] == 2 and r["caller"]["is_vip"]


def test_repeat_call_updates_ticket_and_escalates(conn):
    mk(conn)
    mk(conn)
    r = mk(conn)
    assert not r["is_new"]
    assert len(tickets.list_tickets(conn)) == 1
    assert r["ticket"]["contact_count"] == 3
    assert r["ticket"]["priority"] == 3  # bumped from 4 after repeated contact


def test_unknown_caller(conn):
    r = mk(conn, caller_name="Zed Nobody", company_name="Mystery Inc")
    assert not r["caller"]["recognized"] and r["ticket"]["caller_recognized"] == 0


def test_incident_linking(conn):
    iid = tickets.create_incident(conn, "Print server down", "printing")
    r = mk(conn)
    assert r["incident"]["id"] == iid and r["ticket"]["incident_id"] == iid
    tickets.resolve_incident(conn, iid)
    assert tickets.active_incidents(conn, "printing") == []


def test_status_lifecycle(conn):
    t = mk(conn)["ticket"]
    tickets.set_status(conn, 1, "in_progress", "alice")
    tickets.set_status(conn, 1, "resolved")
    t = tickets.get(conn, 1)
    assert t["status"] == "resolved" and t["assignee"] == "alice" and t["resolved_at"]
    with pytest.raises(ValueError):
        tickets.set_status(conn, 1, "bogus")


def test_escalation_logged(conn):
    mk(conn, category="security", security_concern=True, summary="ransomware note on screen")
    assert conn.execute("SELECT kind FROM escalations").fetchone()["kind"] == "transfer_to_human"


def test_parse_ref():
    assert tickets.parse_ref("T-1004") == 4 and tickets.parse_ref("t 1004") == 4
    assert tickets.parse_ref("abc") is None and tickets.parse_ref("500") is None


def test_kb_matching():
    assert kb.search("VPN error 809")[0]["id"] == "vpn-cannot-connect"
    assert kb.search("forgot my password")[0]["id"] == "password-or-lockout"
    assert kb.search("lost my phone authenticator")[0]["id"] == "lost-phone-mfa"
    assert kb.search("xyzzy") == []
    for a in kb.ARTICLES:
        assert a["sources"] and all(s.startswith("https://") for s in a["sources"])


def test_kb_needs_keyword_hit():
    ids = [a["id"] for a in kb.search("printer offline whole office cannot print already restarted",
                                      operating_system="windows")]
    assert ids == ["printer-offline"]


def test_noncritical_category_capped():
    assert decide(category="printing", scope="whole_company", severity="stopped").priority == 3
    assert decide(category="email", scope="whole_company", severity="stopped").priority == 1
    # security overrides the cap
    assert decide(category="printing", security_concern=True).priority == 1


def test_cli_seed_demo(tmp_path, monkeypatch):
    from msp_agent import cli
    monkeypatch.setenv("MSP_DB", str(tmp_path / "cli.db"))
    c = db.connect()
    for kw in cli.DEMO:
        cli._create(c, **kw)
    assert len(tickets.list_tickets(c)) == 4


def test_zoho_payload_mapping(conn, monkeypatch):
    from msp_agent import zoho
    for k in ("ZOHO_ACCOUNTS_URL", "ZOHO_DESK_API_URL", "ZOHO_DESK_ORG_ID", "ZOHO_CLIENT_ID",
              "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN", "ZOHO_DESK_DEPARTMENT_ID"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("ZOHO_SYNC")
    calls = []

    def fake_api(method, path, body=None, params=None, org=True):
        calls.append((method, path, body))
        return 200, {"id": "999", "ticketNumber": "42", "webUrl": "http://z/42"}

    monkeypatch.setattr(zoho, "api", fake_api)
    r = mk(conn, category="email", severity="stopped", scope="whole_company")  # P1
    out = zoho.sync_ticket(conn, r)
    body = calls[0][2]
    assert out["ok"] and body["priority"] == "High" and body["subject"].startswith("[P1]")
    assert body["channel"] == "Phone" and body["contact"]["lastName"] == "Becker"
    assert tickets.get(conn, 1)["zoho_id"] == "999"
    r2 = mk(conn, category="email", severity="stopped", scope="whole_company")  # repeat -> comment, no new ticket
    assert zoho.sync_ticket(conn, r2)["updated"] and calls[1][1] == "/tickets/999/comments"


def test_zoho_failure_never_raises(conn, monkeypatch):
    from msp_agent import zoho
    for k in ("ZOHO_ACCOUNTS_URL", "ZOHO_DESK_API_URL", "ZOHO_DESK_ORG_ID", "ZOHO_CLIENT_ID",
              "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN", "ZOHO_DESK_DEPARTMENT_ID"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("ZOHO_SYNC")
    monkeypatch.setattr(zoho, "api", lambda *a, **k: (500, {"message": "boom"}))
    out = zoho.sync_ticket(conn, mk(conn))
    assert not out["ok"] and tickets.get(conn, 1)["sync_error"]


def test_resolved_call_is_closed_p4_and_not_merged(conn):
    open_t = mk(conn, category="email")["ticket"]            # an open email ticket for the same person
    r = tickets.create_resolved(conn, caller_name="Tom Becker", company_name="Acme Dental",
                                callback_number="+14155550102", category="email",
                                summary="Outlook hung", resolution="restart")
    t = r["ticket"]
    assert t["id"] != open_t["id"] and t["status"] == "closed" and t["priority"] == 4
    assert t["resolved_at"] and r["decision"].action == "resolved_by_agent"
    assert tickets.get(conn, open_t["id"])["contact_count"] == 1   # open ticket untouched


@pytest.mark.parametrize("cat", ["security", "access"])
def test_resolved_refused_for_security_and_access(conn, cat):
    with pytest.raises(ValueError):
        tickets.create_resolved(conn, caller_name="Tom Becker", company_name="Acme Dental",
                                callback_number="1", category=cat, summary="x", resolution="y")


def test_zoho_status_for_resolved(conn, monkeypatch):
    from msp_agent import zoho
    for k in ("ZOHO_ACCOUNTS_URL", "ZOHO_DESK_API_URL", "ZOHO_DESK_ORG_ID", "ZOHO_CLIENT_ID",
              "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN", "ZOHO_DESK_DEPARTMENT_ID"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("ZOHO_SYNC")
    sent = []
    monkeypatch.setattr(zoho, "api", lambda m, p, b=None, params=None, org=True: (sent.append(b) or (200, {"id": "7"})))
    r = tickets.create_resolved(conn, caller_name="Tom Becker", company_name="Acme Dental",
                                callback_number="+14155550102", category="printing", summary="s", resolution="r")
    assert zoho.sync_ticket(conn, r)["ok"] and sent[0]["status"] == "Closed" and sent[0]["priority"] == "Low"


def test_connection_usable_from_worker_thread(tmp_path):
    """Regression: agent tools create the connection on one thread and use it in asyncio.to_thread."""
    import asyncio
    c = db.connect(tmp_path / "thr.db")

    async def go():
        a = await asyncio.to_thread(mk, c)
        b = await asyncio.to_thread(tickets.create_resolved, c, caller_name="Tom Becker", company_name="Acme Dental",
                                    callback_number="1", category="performance", summary="s", resolution="r")
        return a, b

    a, b = asyncio.run(go())
    assert a["ticket"]["ref"] == "T-1001" and b["ticket"]["status"] == "closed"


def test_kb_os_filter():
    assert kb.search("printer offline", operating_system="mac")[0]["id"] == "printer-mac"
    assert kb.search("printer offline", operating_system="windows")[0]["id"] == "printer-offline"
    assert {h["id"] for h in kb.search("printer offline")} == {"printer-mac", "printer-offline"}
    assert kb.search("wifi not working", operating_system="mac")[0]["id"] == "wifi-mac"
    assert kb.search("wifi not working", operating_system="windows")[0]["id"] == "wifi-windows"


def test_kb_security_routing():
    assert kb.search("we think we got ransomware, files encrypted")[0]["id"] == "ransomware-malware"
    assert kb.search("I clicked a link in a suspicious email")[0]["id"] == "phishing-email"
    assert kb.search("popup says call microsoft your computer is infected")[0]["id"] == "tech-support-scam-popup"


def test_kb_files_are_valid_and_voice_safe():
    ids = [a["id"] for a in kb.ARTICLES]
    assert len(ids) == len(set(ids)) and len(ids) >= 14
    for a in kb.ARTICLES:
        assert a["category"] in escalation.CATEGORIES, a["id"]
        # command-line fixes belong in technician notes, never in the steps spoken to callers
        spoken = " ".join(a["steps"]).lower()
        assert not any(c in spoken for c in ("netsh", "ipconfig", "regedit", "powershell", "sudo ")), a["id"]
        assert all(len(step.split()) <= 70 for step in a["steps"]), f"{a['id']}: a step is too long to say aloud"


def test_kb_require_reviewed_excludes_drafts(monkeypatch):
    monkeypatch.setenv("KB_REQUIRE_REVIEWED", "true")
    assert kb.search("printer offline") == []
    monkeypatch.setenv("KB_REQUIRE_REVIEWED", "false")
    assert kb.search("printer offline")
