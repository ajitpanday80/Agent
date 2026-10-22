"""SQLite storage for the mock PSA. Swap for a real PSA API later."""
import os
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "msp.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    name TEXT NOT NULL, phone TEXT, email TEXT, role TEXT, is_vip INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER REFERENCES companies(id), contact_id INTEGER REFERENCES contacts(id),
    caller_name TEXT NOT NULL, company_name TEXT NOT NULL, callback_number TEXT NOT NULL,
    category TEXT NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL, impact INTEGER NOT NULL, urgency INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', assignee TEXT,
    caller_recognized INTEGER NOT NULL DEFAULT 0, requires_verification INTEGER NOT NULL DEFAULT 0,
    security_flag INTEGER NOT NULL DEFAULT 0, incident_id INTEGER REFERENCES incidents(id),
    contact_count INTEGER NOT NULL DEFAULT 1, action TEXT NOT NULL DEFAULT 'ticket',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    ack_due TEXT NOT NULL, resolve_due TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS ticket_notes (
    id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    author TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS call_log (
    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, backend TEXT NOT NULL, ticket_id TEXT, ticket_ref TEXT,
    caller_name TEXT, company TEXT, category TEXT, priority INTEGER, action TEXT, outcome TEXT NOT NULL, reasons TEXT
);
CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    kind TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
"""

SEED_COMPANIES = ["Acme Dental", "Northwind Logistics"]
SEED_CONTACTS = [
    # company, name, phone, email, role, is_vip
    ("Acme Dental", "Priya Shah", "+14155550101", "priya@acmedental.example", "Owner", 1),
    ("Acme Dental", "Tom Becker", "+14155550102", "tom@acmedental.example", "Front desk", 0),
    ("Northwind Logistics", "Maria Lopez", "+12125550111", "maria@northwind.example", "Office manager", 0),
    ("Northwind Logistics", "James Okoro", "+12125550112", "james@northwind.example", "Dispatcher", 0),
]


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
    if os.getenv("MSP_ENV", "").lower() != "production" and not conn.execute("SELECT 1 FROM companies LIMIT 1").fetchone():
        _seed(conn)   # demo contacts for development only; never in production
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release (SQLite has no IF NOT EXISTS for columns)."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(tickets)")}
    for col in ("zoho_id", "zoho_number", "zoho_url", "sync_error"):
        if col not in have:
            conn.execute(f"ALTER TABLE tickets ADD COLUMN {col} TEXT")
    conn.commit()


def _seed(conn: sqlite3.Connection) -> None:
    for name in SEED_COMPANIES:
        conn.execute("INSERT INTO companies(name) VALUES (?)", (name,))
    for company, name, phone, email, role, vip in SEED_CONTACTS:
        cid = conn.execute("SELECT id FROM companies WHERE name=?", (company,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO contacts(company_id,name,phone,email,role,is_vip) VALUES (?,?,?,?,?,?)",
            (cid, name, phone, email, role, vip),
        )
    conn.commit()
