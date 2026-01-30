import os
import sqlite3
import json
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

APP_NAME = "riconcilia"
# Prefer a writable folder. Streamlit Cloud is usually writable, but we keep it safe.
DEFAULT_DB_DIR = Path(os.environ.get("RECON_DB_DIR", "./data"))
DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = os.environ.get("RECON_DB_PATH", str(DEFAULT_DB_DIR / "recon.db"))

def now_iso():
    return datetime.utcnow().isoformat()

def _connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False, timeout=30)
    # Pragmas for better concurrency on Streamlit
    con.execute("PRAGMA foreign_keys=ON;")
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA busy_timeout=5000;")
    return con

def get_db() -> sqlite3.Connection:
    # If the default path fails (permissions, etc.), fall back to /tmp.
    try:
        return _connect(DB_PATH)
    except sqlite3.OperationalError:
        tmp_path = os.environ.get("RECON_DB_PATH_FALLBACK", "/tmp/recon.db")
        return _connect(tmp_path)

def init_db():
    con = get_db()
    cur = con.cursor()
    cur.executescript("""
    PRAGMA foreign_keys=ON;

    CREATE TABLE IF NOT EXISTS files(
        id INTEGER PRIMARY KEY,
        sha TEXT UNIQUE,
        name TEXT,
        kind TEXT,
        uploaded_at TEXT,
        blob BLOB
    );

    CREATE TABLE IF NOT EXISTS bank_moves(
        id INTEGER PRIMARY KEY,
        file_id INTEGER,
        source TEXT DEFAULT 'csv',
        booking_date TEXT,
        value_date TEXT,
        amount REAL,
        currency TEXT,
        description TEXT,
        counterparty TEXT,
        reference TEXT,
        balance REAL,
        move_hash TEXT UNIQUE,
        FOREIGN KEY(file_id) REFERENCES files(id)
    );

    CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY,
        file_id INTEGER,
        direction TEXT,
        number TEXT,
        invoice_date TEXT,
        party TEXT,
        total REAL,
        currency TEXT,
        is_credit_note INTEGER DEFAULT 0,
        account_code TEXT,
        account_name TEXT,
        status TEXT DEFAULT 'aperta',
        paid_amount REAL DEFAULT 0,
        residual REAL DEFAULT 0,
        invoice_hash TEXT UNIQUE,
        FOREIGN KEY(file_id) REFERENCES files(id)
    );

    CREATE TABLE IF NOT EXISTS sdd(
        id INTEGER PRIMARY KEY,
        file_id INTEGER,
        debtor TEXT,
        amount REAL,
        due_date TEXT,
        endtoend TEXT,
        mandate TEXT,
        creditor TEXT,
        created_at TEXT,
        sdd_hash TEXT UNIQUE,
        FOREIGN KEY(file_id) REFERENCES files(id)
    );

    CREATE TABLE IF NOT EXISTS matches(
        id INTEGER PRIMARY KEY,
        bank_move_id INTEGER,
        invoice_id INTEGER,
        allocated REAL,
        certainty TEXT,
        confirmed INTEGER DEFAULT 0,
        created_at TEXT,
        UNIQUE(bank_move_id, invoice_id),
        FOREIGN KEY(bank_move_id) REFERENCES bank_moves(id),
        FOREIGN KEY(invoice_id) REFERENCES invoices(id)
    );

    CREATE TABLE IF NOT EXISTS sdd_matches(
        id INTEGER PRIMARY KEY,
        bank_move_id INTEGER,
        sdd_id INTEGER,
        allocated REAL,
        certainty TEXT,
        confirmed INTEGER DEFAULT 0,
        created_at TEXT,
        UNIQUE(bank_move_id, sdd_id),
        FOREIGN KEY(bank_move_id) REFERENCES bank_moves(id),
        FOREIGN KEY(sdd_id) REFERENCES sdd(id)
    );

    CREATE TABLE IF NOT EXISTS chart_accounts(
        code TEXT PRIMARY KEY,
        name TEXT,
        kind TEXT
    );

    CREATE TABLE IF NOT EXISTS party_account_map(
        id INTEGER PRIMARY KEY,
        party TEXT,
        direction TEXT,
        account_code TEXT,
        UNIQUE(party, direction),
        FOREIGN KEY(account_code) REFERENCES chart_accounts(code)
    );

    CREATE TABLE IF NOT EXISTS audit_log(
        id INTEGER PRIMARY KEY,
        user TEXT,
        action TEXT,
        entity TEXT,
        entity_id INTEGER,
        before TEXT,
        after TEXT,
        created_at TEXT
    );
    """)
    con.commit()
    con.close()

def ensure_schema():
    """If DB exists but tables are missing (partial schema), recreate missing ones."""
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
        if cur.fetchone() is None:
            con.close()
            init_db()
        else:
            con.close()
    except sqlite3.OperationalError:
        con.close()
        init_db()

def save_file(name: str, kind: str, sha: str, blob: bytes) -> Tuple[int, bool]:
    ensure_schema()
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT id FROM files WHERE sha=?", (sha,))
    row = cur.fetchone()
    if row:
        con.close()
        return int(row[0]), False
    cur.execute("INSERT INTO files(sha,name,kind,uploaded_at,blob) VALUES (?,?,?,?,?)",
                (sha, name, kind, now_iso(), blob))
    con.commit()
    file_id = int(cur.lastrowid)
    con.close()
    return file_id, True

def log_audit(user: str, action: str, entity: str, entity_id: int,
              before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]):
    ensure_schema()
    con = get_db()
    con.execute(
        "INSERT INTO audit_log(user,action,entity,entity_id,before,after,created_at) VALUES (?,?,?,?,?,?,?)",
        (user, action, entity, entity_id, json.dumps(before), json.dumps(after), now_iso())
    )
    con.commit()
    con.close()
