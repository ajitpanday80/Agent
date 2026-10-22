"""Mock ticketing. Swap this module for ConnectWise/Autotask/Halo API calls."""
import json
import time
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent / "tickets.json"


def create(caller_name: str, company: str, callback_number: str,
           summary: str, priority: str, details: str = "") -> str:
    tickets = json.loads(STORE.read_text()) if STORE.exists() else []
    ticket_id = f"T-{1000 + len(tickets) + 1}"
    tickets.append({
        "id": ticket_id,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "caller_name": caller_name,
        "company": company,
        "callback_number": callback_number,
        "summary": summary,
        "priority": priority,
        "details": details,
    })
    STORE.write_text(json.dumps(tickets, indent=2))
    return ticket_id
