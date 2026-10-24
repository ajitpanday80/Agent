import shutil

import pytest

from msp_agent import db, escalation, kb, kb_review, preflight, zoho


def test_sla_from_env(monkeypatch):
    monkeypatch.setenv("MSP_SLA", "1:5/2,2:30/8,3:120/24,4:480/72")
    assert escalation._sla_from_env()[1] == (5, 2) and escalation._sla_from_env()[4] == (480, 72)
    monkeypatch.setenv("MSP_SLA", "1:5/2")
    with pytest.raises(ValueError):
        escalation._sla_from_env()
    monkeypatch.delenv("MSP_SLA")
    assert escalation._sla_from_env() is None


def test_no_demo_contacts_in_production(monkeypatch, tmp_path):
    monkeypatch.setenv("MSP_ENV", "production")
    conn = db.connect(tmp_path / "p.db")
    assert conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0
    monkeypatch.setenv("MSP_ENV", "development")
    assert db.connect(tmp_path / "d.db").execute("SELECT COUNT(*) FROM contacts").fetchone()[0] > 0


def test_cli_refuses_fake_tickets_in_production(monkeypatch):
    from msp_agent import cli
    monkeypatch.setenv("MSP_ENV", "production")
    monkeypatch.setattr("sys.argv", ["cli", "seed-demo"])
    with pytest.raises(SystemExit, match="Refusing"):
        cli.main()


def test_kb_review_approves_only_by_named_person(monkeypatch, tmp_path):
    shutil.copytree(kb.KB_DIR, tmp_path / "kb")
    monkeypatch.setattr(kb, "KB_DIR", tmp_path / "kb")
    with pytest.raises(SystemExit):
        kb_review.approve("wifi-mac", "  ")
    with pytest.raises(SystemExit):
        kb_review.approve("does-not-exist", "Ajit")
    kb_review.approve("wifi-mac", "Ajit Pandey")
    a = kb._parse(tmp_path / "kb" / "wifi-mac.md")
    assert a["status"] == "reviewed" and a["reviewed"].endswith("by Ajit Pandey")
    assert a["steps"] and a["sources"]            # nothing else was damaged


def test_every_shipped_article_is_still_a_draft():
    """The repo must not claim human review that never happened."""
    assert all(a["status"] == "draft" and a["reviewed"] == "never" for a in kb.ARTICLES)


def _prod_env(monkeypatch):
    for k, v in dict(MSP_ENV="production", LIVEKIT_URL="wss://x", LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET="s",
                     DEEPGRAM_API_KEY="d", OPENROUTER_API_KEY="o", KB_REQUIRE_REVIEWED="true", MSP_TZ="Asia/Kolkata",
                     MSP_HOURS="09:00-18:00", MSP_SLA="1:10/4,2:15/8,3:60/48,4:240/120", ALLOW_CALLBACK_ONLY="true",
                     TELEPHONY_VERIFIED="true", LEGAL_REVIEWED="true").items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(escalation, "SLA_IS_DEFAULT", False)
    monkeypatch.setattr(zoho, "enabled", lambda: True)
    monkeypatch.setenv("ZOHO_DESK_ORG_ID", "1")
    monkeypatch.setattr(zoho, "api", lambda *a, **k: (200, {"data": [{"id": 1}]}))
    monkeypatch.setattr(zoho, "directory", lambda force=False: [{"account": {"accountName": "Acme"}}])


def test_preflight_passes_only_when_everything_is_real(monkeypatch):
    _prod_env(monkeypatch)
    # all KB articles are drafts, so the one thing still failing is 'no reviewed article'
    failed = [c.name for c in preflight.run() if c.blocker and not c.ok]
    assert failed == ["At least one human-reviewed KB article"]
    monkeypatch.setattr(kb, "ARTICLES", [{**kb.ARTICLES[0], "status": "reviewed"}])
    assert [c for c in preflight.run() if c.blocker and not c.ok] == []
    preflight.enforce()                              # must not exit


@pytest.mark.parametrize("var,name", [("LEGAL_REVIEWED", "Legal review"), ("TELEPHONY_VERIFIED", "Telephony"),
                                      ("MSP_SLA", "SLA"), ("MSP_TZ", "Business hours"),
                                      ("KB_REQUIRE_REVIEWED", "KB_REQUIRE_REVIEWED")])
def test_each_gate_blocks_startup(monkeypatch, var, name):
    _prod_env(monkeypatch)
    monkeypatch.setattr(kb, "ARTICLES", [{**kb.ARTICLES[0], "status": "reviewed"}])
    monkeypatch.delenv(var)
    if var == "MSP_SLA":
        monkeypatch.setattr(escalation, "SLA_IS_DEFAULT", True)
    with pytest.raises(SystemExit):
        preflight.enforce()
    assert any(name in c.name and not c.ok for c in preflight.run())


def test_preflight_blocks_when_zoho_has_no_company_contacts(monkeypatch):
    _prod_env(monkeypatch)
    monkeypatch.setattr(zoho, "directory", lambda force=False: [{"account": None}])
    assert any("client contacts" in c.name and not c.ok for c in preflight.run())


def test_enforce_is_noop_outside_production(monkeypatch):
    monkeypatch.setenv("MSP_ENV", "development")
    preflight.enforce()


def test_zoho_sample_contact_is_not_a_client(monkeypatch):
    rows = [{"email": "support@zohosupport.com", "lastName": "Lawrence", "account": {"accountName": "Zoho"}},
            {"email": "a@acme.example", "lastName": "Real", "account": {"accountName": "Acme"}}]
    monkeypatch.setattr(zoho, "_list_all", lambda *a, **k: rows)
    assert [r["lastName"] for r in zoho.directory(force=True)] == ["Real"]


def test_kb_retrieval_eval_has_no_misses_or_false_hits():
    from msp_agent import kb_eval
    misses, false_hits = kb_eval.evaluate()
    assert misses == [], misses
    assert false_hits == []
    # every article is the expected answer for at least one phrasing, so none is untested
    covered = {exp for _, _, exp in kb_eval.CASES}
    assert {a["id"] for a in kb.ARTICLES} <= covered | {"ransomware-malware"}, \
        {a["id"] for a in kb.ARTICLES} - covered


def test_no_generic_keywords():
    """Generic phrases make unrelated articles outrank the right one (found this the hard way)."""
    generic = {"not working", "not responding", "broken", "problem", "issue", "error", "help", "doesn't work",
               "won't work"}
    allowed = {"outlook-loading-profile", "onedrive-sync"}      # 'not responding' is the symptom there
    for a in kb.ARTICLES:
        bad = generic & set(a["keywords"])
        assert not bad or a["id"] in allowed, (a["id"], bad)
