"""Small Zoho Desk helper CLI. The Flask console is the primary admin interface."""
import argparse
import os

from dotenv import load_dotenv

from . import db, escalation, tickets

load_dotenv()


def _create(conn, *, name, company, phone="+10000000000", category, summary, details="", scope="single_user",
            severity="degraded", workaround=False, security=False, wants_human=False) -> None:
    r = tickets.create(conn, caller_name=name, company_name=company, callback_number=phone, category=category,
                       summary=summary, details=details, scope=scope, severity=severity,
                       workaround_available=workaround, security_concern=security, caller_requests_human=wants_human)
    t, d = r["ticket"], r["decision"]
    print(f"{'created' if r['is_new'] else 'updated'} {t['ref']}  P{t['priority']}  action={d.action}")
    print("  why:", "; ".join(d.reasons))
    print(f"  Zoho Desk ticket #{t['ref']}  id={t['id']}")


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
    sub.add_parser("handoffs")
    cr = sub.add_parser("create", help="create a Zoho Desk ticket (same rules as the voice agent)")
    cr.add_argument("--name", required=True); cr.add_argument("--company", required=True)
    cr.add_argument("--phone", default="+10000000000")
    cr.add_argument("--category", required=True, choices=escalation.CATEGORIES)
    cr.add_argument("--summary", required=True); cr.add_argument("--details", default="")
    cr.add_argument("--scope", default="single_user", choices=escalation.SCOPES)
    cr.add_argument("--severity", default="degraded", choices=escalation.SEVERITIES)
    cr.add_argument("--workaround", action="store_true")
    cr.add_argument("--security", action="store_true")
    cr.add_argument("--wants-human", action="store_true")
    a = p.parse_args()
    if os.getenv("MSP_ENV", "").lower() == "production" and a.cmd in ("create", "seed-demo"):
        raise SystemExit("Refusing: MSP_ENV=production. Mock/demo tickets are for development only.")
    conn = db.connect()

    if a.cmd == "list":
        for t in tickets.list_tickets(conn, a.status):
            print(f"{t['ref']}  P{t['priority']}  {t['status']:<19} {t['category']:<12} {t['company_name']}: {t['summary']}")
    elif a.cmd == "show":
        t = tickets.get(conn, tickets.parse_ref(a.ref))
        if not t:
            raise SystemExit("not found")
        for k in ("ref", "status", "priority", "category", "caller_name", "company_name", "callback_number",
                  "summary", "details", "assignee", "ack_due"):
            print(f"{k:>22}: {t.get(k, '')}")
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
                security=a.security, wants_human=a.wants_human)
    elif a.cmd == "handoffs":
        for r in conn.execute("SELECT * FROM handoffs ORDER BY id DESC"):
            print(f"{r['created_at']}  {r['kind']}  {r['status']}  #{r['ticket_id'] or 'no Zoho ticket'}  {r['summary']}")


if __name__ == "__main__":
    main()
