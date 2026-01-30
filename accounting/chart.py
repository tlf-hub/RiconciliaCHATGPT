import pandas as pd
from typing import Optional
from db import get_db

DEFAULT_CHART = [
    ("1010", "Banca c/c", "asset"),
    ("1200", "Crediti v/clienti", "asset"),
    ("1990", "Sospesi da riconciliare", "suspense"),
    ("2200", "Debiti v/fornitori", "liability"),
    ("3000", "Patrimonio netto", "equity"),
    ("7000", "Ricavi vendite e prestazioni", "revenue"),
    ("7010", "Ricavi servizi", "revenue"),
    ("6000", "Acquisti", "expense"),
    ("6100", "Servizi", "expense"),
    ("6200", "Godimento beni di terzi", "expense"),
    ("6500", "Oneri diversi di gestione", "expense"),
    ("6600", "Interessi passivi", "expense"),
]

def ensure_default_chart():
    con = get_db()
    cur = con.cursor()
    for code, name, kind in DEFAULT_CHART:
        cur.execute("INSERT OR IGNORE INTO chart_accounts(code,name,kind) VALUES (?,?,?)", (code, name, kind))
    con.commit()
    con.close()

def get_chart_df() -> pd.DataFrame:
    con = get_db()
    df = pd.read_sql("SELECT * FROM chart_accounts ORDER BY code", con)
    con.close()
    return df

def upsert_account(code: str, name: str, kind: str):
    con = get_db()
    con.execute(
        "INSERT INTO chart_accounts(code,name,kind) VALUES (?,?,?) "
        "ON CONFLICT(code) DO UPDATE SET name=excluded.name, kind=excluded.kind",
        (code, name, kind)
    )
    con.commit()
    con.close()

def set_party_map(party: str, direction: str, account_code: str):
    con = get_db()
    con.execute(
        "INSERT INTO party_account_map(party,direction,account_code) VALUES (?,?,?) "
        "ON CONFLICT(party,direction) DO UPDATE SET account_code=excluded.account_code",
        (party, direction, account_code)
    )
    con.commit()
    con.close()

def get_party_account_code(party: str, direction: str) -> Optional[str]:
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT account_code FROM party_account_map WHERE party=? AND direction=?", (party, direction))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None

def get_account_name(code: str) -> Optional[str]:
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT name FROM chart_accounts WHERE code=?", (code,))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None
