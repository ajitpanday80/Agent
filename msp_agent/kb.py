"""L1 knowledge base. Steps are paraphrased from the cited vendor/government pages and shortened for
voice. Keep each step small enough to say out loud. Review against current docs before production use."""
import re
import json
import math
import sqlite3
from pathlib import Path

ARTICLES = [
    {
        "id": "outlook-loading-profile",
        "title": "Outlook stuck on Loading Profile or not responding",
        "category": "email",
        "keywords": ["outlook", "loading profile", "not responding", "freezes", "hangs", "email", "mail", "won't open"],
        "steps": [
            "Close Outlook completely, then open it again. If it will not close, end Outlook in Task Manager.",
            "Hold the Control key while opening Outlook, or run 'outlook.exe /safe'. This starts Outlook in safe mode, which turns off add-ins.",
            "If safe mode works, an add-in is the likely cause. Create a ticket so a technician can find it. Do not remove add-ins by phone; disabling is safer than removing.",
        ],
        "escalate_if": "Still failing in safe mode, or the caller sees a data file or profile corruption message. A technician may need to repair the data file; do not walk the caller through repair tools.",
        "sources": [
            "https://support.microsoft.com/en-us/office/open-outlook-in-safe-mode-f394c2ee-b2af-e97a-64ee-35cd24414811",
            "https://support.microsoft.com/en-us/outlook/getstarted/outlook-for-windows-not-responding-hangs-freezes-or-stops-working",
        ],
    },
    {
        "id": "printer-offline",
        "title": "Printer shows offline or will not print",
        "category": "printing",
        "keywords": ["printer", "print", "printing", "offline", "paper jam", "queue", "spooler"],
        "steps": [
            "Check the printer is on, has no error or paper jam, and is on the same network as the computer.",
            "Turn the printer off, unplug it for about thirty seconds, plug it back in and wait for it to be ready.",
            "On the computer, open the print queue and make sure 'Pause printing' and 'Use printer offline' are not ticked, then cancel any stuck jobs.",
            "Restart the computer and try one print.",
        ],
        "escalate_if": "Still offline after these steps, or several people cannot print (possible print server or network issue). Technician handles spooler service restart, re-adding the printer and drivers.",
        "sources": [
            "https://support.microsoft.com/en-us/windows/hardware/printer/troubleshooting-offline-printer-problems-in-windows",
            "https://support.microsoft.com/en-us/windows/hardware/printer/fix-printer-connection-and-printing-problems-in-windows",
        ],
    },
    {
        "id": "slow-pc",
        "title": "Computer running slowly",
        "category": "performance",
        "keywords": ["slow", "laggy", "lag", "sluggish", "performance", "takes forever", "freezing", "startup"],
        "steps": [
            "Save work, close programs and browser tabs that are not needed, and restart the computer.",
            "If still slow, ask whether the drive is nearly full. Storage Sense in Settings under System, Storage can clear temporary files.",
            "Ask whether it is slow at startup. Too many startup apps can cause that; a technician can review them.",
        ],
        "escalate_if": "Still slow after a restart, slow for several people, or any sign of malware such as pop-ups or unknown programs. Treat sudden slowness plus strange behavior as a possible security issue.",
        "sources": [
            "https://support.microsoft.com/en-us/windows/experience/performance-optimization/tips-to-improve-pc-performance-in-windows",
        ],
    },
    {
        "id": "vpn-cannot-connect",
        "title": "VPN will not connect",
        "category": "network_vpn",
        "keywords": ["vpn", "remote access", "work from home", "error 720", "error 800", "error 809", "error 721", "error 812", "can't connect"],
        "steps": [
            "First ask for the exact error message or number, and whether regular internet works without the VPN.",
            "Disconnect, close the VPN app fully, reopen it and try once more. Restart the computer if that fails.",
            "Ask if they are on a hotel, cafe or other public network. Some networks block VPN traffic; trying a phone hotspot is a good test.",
        ],
        "escalate_if": "Same error after these steps, or several users cannot connect (possible firewall or VPN server issue). Error numbers help the technician: 720 usually means a local network adapter binding issue; 800 or 809 usually means the VPN server is unreachable or ports are blocked; 721 points to a blocked PPTP protocol; 812 or 13801 point to an authentication or certificate mismatch.",
        "sources": [
            "https://learn.microsoft.com/en-us/troubleshoot/windows-client/networking/error-when-establishing-vpn-connection",
            "https://learn.microsoft.com/en-us/troubleshoot/windows-server/networking/troubleshoot-error-720-when-establishing-a-vpn-connection",
        ],
    },
    {
        "id": "password-or-lockout",
        "title": "Forgot password or account locked out",
        "category": "access",
        "keywords": ["password", "locked out", "lockout", "account locked", "can't sign in", "cannot log in", "forgot", "reset password", "unlock"],
        "steps": [
            "Do NOT reset or unlock anything. Help-desk resets are a common social-engineering target.",
            "If the company has self-service password reset turned on, the caller can use the 'Can't access your account' link on the sign-in page. Only mention this if you know their company uses it; otherwise do not promise it.",
            "Create an access ticket. A technician will call back and verify identity before any change.",
        ],
        "escalate_if": "Always a ticket for a technician. Raise priority if the caller is a VIP or work is fully stopped; priority is set by the system.",
        "sources": [
            "https://learn.microsoft.com/training/modules/allow-users-reset-their-password/2-self-service-password-reset",
            "https://specopssoft.com/blog/helpdesk-password-reset-best-practices/",
        ],
    },
    {
        "id": "lost-phone-mfa",
        "title": "Lost or replaced phone, cannot approve sign-in (MFA)",
        "category": "access",
        "keywords": ["mfa", "authenticator", "lost phone", "new phone", "two factor", "2fa", "verification code", "approve sign-in", "stolen phone"],
        "steps": [
            "Do NOT reset MFA. This is a high-risk request for impersonation.",
            "Ask if they can sign in with another method they registered before, such as a text or call. If so, they can add the new phone at their security info page.",
            "Otherwise create an access ticket. A technician must verify identity, then an administrator can have the user re-register or issue a temporary access pass.",
            "If the phone was stolen, tell the caller a technician will treat this as a security matter.",
        ],
        "escalate_if": "Stolen phone means treat as a security incident. Any attempt to bypass verification is a reason to escalate.",
        "sources": [
            "https://learn.microsoft.com/en-us/answers/questions/5428281/cant-access-my-microsoft-account-because-i-lost-my",
        ],
        "notes": "Source is a community Q&A thread; confirm the admin procedure against Microsoft Entra documentation for your tenant.",
    },
    {
        "id": "ransomware-phishing",
        "title": "Suspected ransomware, malware, or phishing click",
        "category": "security",
        "keywords": ["ransomware", "ransom", "encrypted", "files locked", "virus", "malware", "phishing", "clicked a link", "hacked", "suspicious", "compromised", "bitcoin"],
        "steps": [
            "Stay calm and stay on the line. This is a priority one security incident and a human technician must be involved.",
            "Ask the caller to disconnect the affected computer from the network by unplugging the cable or turning off Wi-Fi. Do not power it off, because that can destroy evidence.",
            "Ask them not to delete files, not to click or forward anything suspicious, and not to pay or reply to any ransom demand.",
            "Collect: which computers, what they saw, what time it started, and whether they entered a password.",
        ],
        "escalate_if": "Always. Immediate human escalation.",
        "sources": [
            "https://www.cisa.gov/stopransomware/ive-been-hit-ransomware",
        ],
    },
]


STOPWORDS = {"a", "an", "the", "or", "and", "of", "to", "not", "cannot", "can", "will", "is", "my", "our", "we", "in", "on", "it"}


def markdown_articles() -> list[dict]:
    """Load the repository's source articles for the admin review queue; they are always seeded as drafts."""
    folder = Path(__file__).resolve().parent.parent / "kb"
    articles = []
    for path in sorted(folder.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
        if not match:
            continue
        meta: dict[str, str] = {}
        sources: list[str] = []
        key = ""
        for line in match.group(1).splitlines():
            if line.startswith("  - ") and key == "sources":
                sources.append(line[4:].strip())
            elif ":" in line and not line.startswith(" "):
                key, value = line.split(":", 1)
                key = key.strip()
                if key == "sources":
                    continue
                meta[key] = value.strip()
        sections: dict[str, str] = {}
        current = ""
        for line in match.group(2).splitlines():
            if line.startswith("## "):
                current = line[3:].strip().lower()
                sections[current] = ""
            elif current:
                sections[current] += line + "\n"
        steps = [re.sub(r"^\d+\.\s*", "", line).strip()
                 for line in sections.get("steps", "").splitlines() if line.strip()]
        title, category, article_id = meta.get("title"), meta.get("category"), meta.get("id")
        if not title or not category or not article_id or not steps or not sources:
            continue
        escalation_notes = sections.get("escalate if", "").strip()
        content = " ".join((title, meta.get("keywords", "").replace(",", " "), *steps, escalation_notes))
        articles.append({"id": article_id, "title": title, "category": category, "content": content,
                         "steps": steps, "sources": sources, "escalate_if": escalation_notes,
                         "technician_notes": sections.get("technician notes", "").strip()})
    return articles


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def search(query: str, limit: int = 2) -> list[dict]:
    q = query.lower()
    qt = _tokens(q)
    scored = []
    for a in ARTICLES:
        score = 0
        for k in a["keywords"]:
            if " " in k or "'" in k:
                score += 3 if k in q else 0
            elif k in qt:
                score += 2
        if score:  # a keyword hit is required; title overlap only breaks ties
            score += len(qt & _tokens(a["title"]) - STOPWORDS)
            scored.append((score, a))
    scored.sort(key=lambda s: -s[0])
    return [a for _, a in scored[:limit]]


def search_index(query: str, conn: sqlite3.Connection, limit: int = 3,
                 company_name: str | None = None) -> list[dict]:
    """Local BM25-style retrieval over the editable knowledge library; results ground voice answers."""
    terms = _tokens(query) - STOPWORDS
    if not terms:
        return []
    if company_name == "*":
        rows = [dict(r) for r in conn.execute("SELECT * FROM kb_articles WHERE enabled=1 ORDER BY updated_at DESC")]
    elif company_name:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM kb_articles WHERE enabled=1 AND (company_name='' OR lower(company_name)=lower(?)) "
            "ORDER BY CASE WHEN lower(company_name)=lower(?) THEN 0 ELSE 1 END,updated_at DESC",
            (company_name, company_name))]
    else:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM kb_articles WHERE enabled=1 AND company_name='' ORDER BY updated_at DESC")]
    # Never fall back to bundled advice: only reviewed, enabled DB articles are production content.
    if not rows:
        return []
    corpus = []
    for row in rows:
        try:
            steps = json.loads(row["steps"])
            sources = json.loads(row["sources"])
        except (ValueError, TypeError):
            steps, sources = [], []
        text = " ".join((row["title"], row["category"], row["content"], " ".join(steps)))
        corpus.append((row, steps, sources, _tokens(text)))
    avg_len = max(1, sum(len(doc[3]) for doc in corpus) / len(corpus))
    scored = []
    for row, steps, sources, tokens in corpus:
        matched = terms & tokens
        if not matched:
            continue
        score = 0.0
        for term in matched:
            df = sum(term in doc[3] for doc in corpus)
            idf = math.log(1 + (len(corpus) - df + 0.5) / (df + 0.5))
            tf = sum(1 for t in re.findall(r"[a-z0-9']+", (row["title"] + " " + row["content"]).lower()) if t == term)
            score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * len(tokens) / avg_len))
        if score > 0:
            if company_name not in (None, "*") and row.get("company_name"):
                score *= 1.25
            scored.append((score, row, steps, sources))
    scored.sort(key=lambda item: (-item[0], item[1]["title"].lower()))
    return [{"id": row["id"], "title": row["title"], "category": row["category"],
             "company_name": row.get("company_name", ""), "content": row["content"], "steps": steps, "sources": sources,
             "escalate_if": row.get("escalate_if", ""),
             "score": round(score, 4)} for score, row, steps, sources in scored[:limit]]


def format_for_agent(article: dict) -> str:
    steps = " ".join(f"Step {i}: {s}" for i, s in enumerate(article["steps"], 1))
    if article.get("content"):
        scope = f"For {article['company_name']}. " if article.get("company_name") else ""
        escalation = article.get("escalate_if", "")
        extra = f" Escalate if: {escalation}." if escalation else ""
        return f"{scope}{article['title']}. {steps or article['content']}.{extra} Sources: {', '.join(article.get('sources', []))}"
    return f"{article['title']}. {steps} Escalate if: {article['escalate_if']}"
