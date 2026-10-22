"""Read-only Zoho Desk connection check. Prints no secrets.
    python scripts/zoho_check.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from msp_agent import zoho  # noqa: E402

if not zoho.enabled():
    raise SystemExit("Zoho is not configured: check the ZOHO_* lines in .env")
status, resp = zoho.api("GET", "/organizations", org=False)
print("organizations:", status, [(o.get("companyName"), o.get("id")) for o in resp.get("data", [])] or resp)
status, resp = zoho.api("GET", "/departments")
print("departments:", status, [(d.get("name"), d.get("id")) for d in resp.get("data", [])] or resp)
print("configured org id:", os.getenv("ZOHO_DESK_ORG_ID"))
