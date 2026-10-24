"""Human review of KB articles.   python -m msp_agent.kb_review list | approve <id> --by "Your Name"
Only a person who has verified the steps against the cited sources and your own environment should approve.
Approval rewrites the article's front matter (status: reviewed, reviewed: DATE by NAME)."""
import argparse
import re
from datetime import date

from . import kb


def approve(article_id: str, by: str) -> None:
    path = kb.KB_DIR / f"{article_id}.md"
    if not path.exists():
        raise SystemExit(f"no such article: {article_id}")
    if not by.strip():
        raise SystemExit("--by is required")
    s = path.read_text()
    s = re.sub(r"^reviewed:.*$", f"reviewed: {date.today().isoformat()} by {by.strip()}", s, count=1, flags=re.M)
    s = re.sub(r"^status:.*$", "status: reviewed", s, count=1, flags=re.M)
    path.write_text(s)


def main() -> None:
    p = argparse.ArgumentParser(prog="msp_agent.kb_review")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    ap = sub.add_parser("approve")
    ap.add_argument("id")
    ap.add_argument("--by", required=True)
    a = p.parse_args()
    if a.cmd == "list":
        for x in kb.ARTICLES:
            print(f"{x['status']:<9} {x['os']:<8} {x['id']:<28} reviewed: {x['reviewed']}")
    else:
        approve(a.id, a.by)
        print(f"approved {a.id}")


if __name__ == "__main__":
    main()
