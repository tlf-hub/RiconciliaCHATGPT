import sqlite3, json
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

DB_PATH = "recon.db"

def get_db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def now_iso():
    return datetime.utcnow().isoformat()

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
        UNIQUE(booking_date, amount, COALESCE(reference,''), COALESCE(description,'')),
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
        UNIQUE(direction, number, total, invoice_date, COALESCE(party,'')),
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
        FOREIGN KEY(bank_move_id) REFERENCES bank_moves(id),
        FOREIGN KEY(invoice_id) REFERENCES invoices(id),
        UNIQUE(bank_move_id, invoice_id)
    );

    CREATE TABLE IF NOT EXISTS sdd_matches(
        id INTEGER PRIMARY KEY,
        bank_move_id INTEGER,
        sdd_id INTEGER,
        allocated REAL,
        certainty TEXT,
        confirmed INTEGER DEFAULT 0,
        created_at TEXT,
        FOREIGN KEY(bank_move_id) REFERENCES bank_moves(id),
        FOREIGN KEY(sdd_id) REFERENCES sdd(id),
        UNIQUE(bank_move_id, sdd_id)
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

def save_file(name: str, kind: str, sha: str, blob: bytes) -> Tuple[int, bool]:
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
    fid = int(cur.lastrowid)
    con.close()
    return fid, True

def log_audit(user: str, action: str, entity: str, entity_id: int,
              before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]):
    con = get_db()
    con.execute(
        "INSERT INTO audit_log(user,action,entity,entity_id,before,after,created_at) VALUES (?,?,?,?,?,?,?)",
        (user, action, entity, entity_id, json.dumps(before), json.dumps(after), now_iso())
    )
    con.commit()
    con.close()
