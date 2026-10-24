"""SQLite storage for KB, incidents, intake, configuration, and audit; tickets live in Zoho Desk."""
import os
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "msp.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS kb_articles (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL, company_name TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL, steps TEXT NOT NULL DEFAULT '[]', sources TEXT NOT NULL DEFAULT '[]',
    escalate_if TEXT NOT NULL DEFAULT '', technician_notes TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0, review_status TEXT NOT NULL DEFAULT 'draft',
    reviewed_by TEXT NOT NULL DEFAULT '', reviewed_at TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY, ticket_id TEXT, kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued', summary TEXT NOT NULL, callback_number TEXT NOT NULL DEFAULT '',
    transcript TEXT NOT NULL DEFAULT '', assignee TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contact_flags (
    zoho_contact_id TEXT PRIMARY KEY, is_vip INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_links (
    fingerprint TEXT NOT NULL, category TEXT NOT NULL, zoho_ticket_id TEXT NOT NULL,
    contact_count INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL,
    PRIMARY KEY(fingerprint,category)
);
"""

def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    p = Path(path or os.getenv("MSP_DB") or DEFAULT_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Agent tools open the connection on the event-loop thread and run the work in a worker thread.
    # Use is strictly sequential per call, so sharing across threads is safe.
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    _seed_knowledge(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add operational KB metadata without touching legacy ticket tables."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(kb_articles)")}
    additions = {
        "company_name": "TEXT NOT NULL DEFAULT ''",
        "escalate_if": "TEXT NOT NULL DEFAULT ''",
        "technician_notes": "TEXT NOT NULL DEFAULT ''",
        "review_status": "TEXT NOT NULL DEFAULT 'draft'",
        "reviewed_by": "TEXT NOT NULL DEFAULT ''",
        "reviewed_at": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE kb_articles ADD COLUMN {name} {definition}")
    # Existing advice predates the human review workflow. Disable it until an admin reviews its sources and client fit.
    conn.execute("UPDATE kb_articles SET enabled=0 WHERE review_status='draft'")
    conn.commit()


def _seed_knowledge(conn: sqlite3.Connection) -> None:
    import json
    from .kb import ARTICLES
    from .kb import markdown_articles
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for article in [*ARTICLES, *markdown_articles()]:
        content = article.get("content") or " ".join(
            [article["title"], " ".join(article.get("keywords", [])),
             " ".join(article.get("steps", [])), article.get("escalate_if", "")]
        )
        conn.execute(
            "INSERT OR IGNORE INTO kb_articles(id,title,category,company_name,content,steps,sources,escalate_if,"
            "technician_notes,enabled,review_status,reviewed_by,reviewed_at,created_at,updated_at) "
            "VALUES(?,?,?,'',?,?,?,?,?,0,'draft','',NULL,?,?)",
            (article["id"], article["title"], article["category"], content,
             json.dumps(article.get("steps", [])), json.dumps(article.get("sources", [])),
             article.get("escalate_if", ""), article.get("technician_notes", ""), now, now),
        )
    conn.commit()
