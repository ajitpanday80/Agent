"""Tiny admin view of the mock PSA.  python -m msp_agent.cli --help"""
import argparse

from dotenv import load_dotenv

from . import db, escalation, tickets, zoho

load_dotenv()


DEMO = [
    dict(name="Tom Becker", company="Acme Dental", phone="+14155550102", category="printing",
         summary="Front desk printer offline", scope="single_user", severity="degraded"),
    dict(name="Maria Lopez", company="Northwind Logistics", phone="+12125550111", category="access",
         summary="Locked out of account", scope="single_user", severity="stopped"),
    dict(name="Priya Shah", company="Acme Dental", phone="+14155550101", category="email",
         summary="Outlook will not open", scope="single_user", severity="stopped"),
    dict(name="James Okoro", company="Northwind Logistics", phone="+12125550112", category="network_vpn",
         summary="VPN down for all dispatchers", scope="several_users", severity="stopped"),
]


def _create(conn, *, name, company, phone="+10000000000", category, summary, details="", scope="single_user",
            severity="degraded", workaround=False, security=False, wants_human=False, push_zoho=False) -> None:
    r = tickets.create(conn, caller_name=name, company_name=company, callback_number=phone, category=category,
                       summary=summary, details=details, scope=scope, severity=severity,
                       workaround_available=workaround, security_concern=security, caller_requests_human=wants_human)
    t, d = r["ticket"], r["decision"]
    print(f"{'created' if r['is_new'] else 'updated'} {t['ref']}  P{t['priority']}  action={d.action}")
    print("  why:", "; ".join(d.reasons))
    if not push_zoho:
        print("  (local only; add --zoho to also push to Zoho Desk)")
        return
    sync = zoho.sync_ticket(conn, r)
    if sync.get("ok"):
        print(f"  zoho: {sync.get('number') or sync['zoho_id']}  {sync.get('url') or ''}")
    elif not sync.get("skipped"):
        print("  zoho sync FAILED:", sync.get("error"))


def main() -> None:
    p = argparse.ArgumentParser(prog="msp_agent.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list"); ls.add_argument("--status")
    sh = sub.add_parser("show"); sh.add_argument("ref")
    nt = sub.add_parser("note"); nt.add_argument("ref"); nt.add_argument("text")
    st = sub.add_parser("status"); st.add_argument("ref"); st.add_argument("status", choices=tickets.STATUSES)
    st.add_argument("--assignee")
    ia = sub.add_parser("incident-add"); ia.add_argument("title"); ia.add_argument("category")
    ir = sub.add_parser("incident-resolve"); ir.add_argument("id", type=int)
    sub.add_parser("escalations")
    cr = sub.add_parser("create", help="create a mock ticket by hand (same rules as the voice agent)")
    cr.add_argument("--name", required=True); cr.add_argument("--company", required=True)
    cr.add_argument("--phone", default="+10000000000")
    cr.add_argument("--category", required=True, choices=escalation.CATEGORIES)
    cr.add_argument("--summary", required=True); cr.add_argument("--details", default="")
    cr.add_argument("--scope", default="single_user", choices=escalation.SCOPES)
    cr.add_argument("--severity", default="degraded", choices=escalation.SEVERITIES)
    cr.add_argument("--workaround", action="store_true")
    cr.add_argument("--security", action="store_true")
    cr.add_argument("--wants-human", action="store_true")
    sub.add_parser("calls", help="call log: one row per ticket action on a call")
    sub.add_parser("retry-sync", help="push tickets saved locally while Zoho was down")
    sd = sub.add_parser("seed-demo", help="add a few example tickets (local only unless --zoho)")
    sd.add_argument("--zoho", action="store_true", help="ALSO push these fake tickets to Zoho Desk")
    cr.add_argument("--zoho", action="store_true", help="ALSO push this ticket to Zoho Desk")
    a = p.parse_args()
    conn = db.connect()

    if a.cmd == "list":
        for t in tickets.list_tickets(conn, a.status):
            print(f"{t['ref']}  P{t['priority']}  {t['status']:<19} {t['category']:<12} {t['company_name']}: {t['summary']}")
    elif a.cmd == "show":
        t = tickets.get(conn, tickets.parse_ref(a.ref))
        if not t:
            raise SystemExit("not found")
        for k in ("ref", "status", "priority", "category", "caller_name", "company_name", "callback_number",
                  "summary", "details", "action", "caller_recognized", "requires_verification",
                  "security_flag", "contact_count", "assignee", "ack_due", "resolve_due"):
            print(f"{k:>22}: {t[k]}")
        for n in t["notes"]:
            print(f"  [{n['created_at']}] {n['author']}: {n['body']}")
    elif a.cmd == "note":
        tickets.add_note(conn, tickets.parse_ref(a.ref), "technician", a.text)
    elif a.cmd == "status":
        tickets.set_status(conn, tickets.parse_ref(a.ref), a.status, a.assignee)
    elif a.cmd == "incident-add":
        print("incident", tickets.create_incident(conn, a.title, a.category))
    elif a.cmd == "incident-resolve":
        tickets.resolve_incident(conn, a.id)
    elif a.cmd == "create":
        _create(conn, name=a.name, company=a.company, phone=a.phone, category=a.category, summary=a.summary,
                details=a.details, scope=a.scope, severity=a.severity, workaround=a.workaround,
                security=a.security, wants_human=a.wants_human, push_zoho=a.zoho)
    elif a.cmd == "seed-demo":
        for kw in DEMO:
            _create(conn, push_zoho=a.zoho, **kw)
    elif a.cmd == "escalations":
        rows = conn.execute("SELECT * FROM call_log WHERE action IN ('transfer_to_human','page_oncall',"
                            "'callback_next_business_day') ORDER BY id").fetchall()
        for r in rows:
            print(f"{r['created_at']}  {r['backend']}:{r['ticket_ref']}  {r['action']}  {r['reasons']}")
        if not rows:
            print("no escalations logged")
    elif a.cmd == "calls":
        for r in conn.execute("SELECT * FROM call_log ORDER BY id"):
            print(f"{r['created_at']}  {r['backend']:<5} {str(r['ticket_ref'] or '-'):<8} {r['outcome']:<18} "
                  f"P{r['priority'] or '-'} {r['category'] or '-':<12} {r['caller_name'] or '?'} / {r['company'] or '?'}")
    elif a.cmd == "retry-sync":
        if not zoho.enabled():
            raise SystemExit("Zoho is not configured")
        res = zoho.push_pending(conn)
        for ref, msg in res:
            print(ref, msg)
        if not res:
            print("nothing pending")


if __name__ == "__main__":
    main()
