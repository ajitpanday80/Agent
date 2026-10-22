"""Tiny keyword knowledge base. Replace with your PSA KB / vector store later."""

ARTICLES = [
    {
        "title": "Outlook not opening or stuck on 'Loading profile'",
        "keywords": ["outlook", "loading profile", "email not opening", "mail"],
        "steps": "Close Outlook fully, reopen it, and if it still hangs restart the computer. "
                 "If it persists, create a ticket so a technician can rebuild the profile.",
    },
    {
        "title": "VPN will not connect",
        "keywords": ["vpn", "remote access", "cannot connect"],
        "steps": "Check the internet works without VPN, disconnect and reconnect the VPN client, "
                 "then restart the client. If it still fails, create a ticket and note the exact error text.",
    },
    {
        "title": "Printer offline",
        "keywords": ["printer", "print", "offline"],
        "steps": "Check the printer is powered on and shows no error. Restart the printer, wait one minute, "
                 "and retry. If it is still offline, create a ticket with the printer name and location.",
    },
    {
        "title": "Slow computer",
        "keywords": ["slow", "freezing", "laggy", "performance"],
        "steps": "Save work and restart the computer; many slowdowns clear after a restart. "
                 "If it is still slow after restart, create a ticket.",
    },
]


def search(query: str) -> list[dict]:
    q = query.lower()
    hits = [a for a in ARTICLES if any(k in q for k in a["keywords"])]
    return hits
