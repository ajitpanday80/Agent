"""Tiny admin view of the mock PSA.  python -m msp_agent.cli --help"""
import argparse

from . import db, tickets


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
    elif a.cmd == "escalations":
        for r in conn.execute("SELECT * FROM escalations ORDER BY id"):
            print(f"{r['created_at']}  {tickets.ref(r['ticket_id'])}  {r['kind']}  {r['reason']}")


if __name__ == "__main__":
    main()
