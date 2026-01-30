
# app.py
# Riconciliazione contabile automatica (estratto conto + FatturaPA + SEPA SDD + esiti insoluti)
# Deploy: streamlit run app.py
# DB: SQLite locale (recon.db)

import io
import os
import re
import csv
import json
import zipfile
import hashlib
import sqlite3
import unicodedata
from decimal import Decimal, InvalidOperation
from datetime import datetime, date
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from xml.etree import ElementTree as ET

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors


APP_TITLE = "Riconciliazione Contabile Automatica"
DB_PATH = os.environ.get("RECON_DB_PATH", "recon.db")
CURRENCY_DEFAULT = "EUR"
AMOUNT_TOL = 0.02  # tolleranza arrotondamenti (2 cent)
DATE_WINDOW_DAYS = 10


# -------------------------
# Utility: normalizzazione
# -------------------------
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def norm_text(s: str) -> str:
    s = (s or "").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def similarity(a: str, b: str) -> int:
    a2, b2 = norm_text(a), norm_text(b)
    if not a2 or not b2:
        return 0
    return int(round(100 * SequenceMatcher(None, a2, b2).ratio()))


def parse_decimal(x) -> Optional[float]:
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    # pulizia simboli e spazi
    s = s.replace("€", "").replace("EUR", "").replace("eur", "").strip()
    s = s.replace("\u00a0", " ").replace(" ", "")
    # gestioni migliaia/decimali italiane
    if "," in s and "." in s:
        # scegli l'ultimo separatore come decimale
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # "1234,56" oppure "1.234,56"
        s = s.replace(".", "").replace(",", ".")
    # segni strani
    s = s.replace("+", "")
    try:
        return float(Decimal(s))
    except (InvalidOperation, ValueError):
        return None


def parse_date_any(x) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    ts = pd.to_datetime(s, dayfirst=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date().isoformat()


def money_eq(a: Optional[float], b: Optional[float], tol: float = AMOUNT_TOL) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


# -------------------------
# SQLite
# -------------------------
@st.cache_resource
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS profile(
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS files(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        kind TEXT,
        sha256 TEXT UNIQUE,
        uploaded_at TEXT,
        content BLOB
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bank_tx(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tx_hash TEXT UNIQUE,
        tx_date TEXT,
        value_date TEXT,
        description TEXT,
        amount REAL,
        currency TEXT,
        balance REAL,
        counterparty_name TEXT,
        counterparty_iban TEXT,
        raw_json TEXT,
        file_id INTEGER,
        created_at TEXT,
        FOREIGN KEY(file_id) REFERENCES files(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bank_checks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id INTEGER,
        tx_hash TEXT,
        check_type TEXT,
        expected REAL,
        found REAL,
        ok INTEGER,
        note TEXT,
        created_at TEXT,
        FOREIGN KEY(file_id) REFERENCES files(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inv_hash TEXT UNIQUE,
        direction TEXT,          -- EMESSA / RICEVUTA / UNKNOWN
        party_name TEXT,
        party_name_norm TEXT,
        party_vat TEXT,
        invoice_number TEXT,
        invoice_date TEXT,
        due_date TEXT,
        total_amount REAL,
        currency TEXT,
        e2e_reference TEXT,
        raw_xml TEXT,
        file_id INTEGER,
        created_at TEXT,
        FOREIGN KEY(file_id) REFERENCES files(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sdd(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sdd_hash TEXT UNIQUE,
        message_id TEXT,
        collection_date TEXT,
        creditor_name TEXT,
        creditor_id TEXT,
        debtor_name TEXT,
        debtor_iban TEXT,
        mandate_id TEXT,
        end_to_end_id TEXT,
        amount REAL,
        currency TEXT,
        remittance TEXT,
        raw_xml TEXT,
        file_id INTEGER,
        created_at TEXT,
        FOREIGN KEY(file_id) REFERENCES files(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sdd_outcomes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        out_hash TEXT UNIQUE,
        end_to_end_id TEXT,
        status TEXT,
        reason_code TEXT,
        reason_text TEXT,
        value_date TEXT,
        amount REAL,
        currency TEXT,
        raw_xml TEXT,
        file_id INTEGER,
        created_at TEXT,
        FOREIGN KEY(file_id) REFERENCES files(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS party_aliases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alias_norm TEXT UNIQUE,
        canonical TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS matches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bank_tx_id INTEGER,
        doc_type TEXT,          -- INVOICE / SDD / SDD_OUTCOME / NONE
        doc_id INTEGER,
        confidence INTEGER,
        status TEXT,            -- CONFIRMED / SUGGESTED / IGNORED
        allocated_amount REAL,
        note TEXT,
        created_at TEXT,
        UNIQUE(bank_tx_id, doc_type, doc_id),
        FOREIGN KEY(bank_tx_id) REFERENCES bank_tx(id)
    )
    """)

    conn.commit()


def db_get_profile(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM profile WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def db_set_profile(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO profile(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))
    conn.commit()


# -------------------------
# Ingest file + dedup
# -------------------------
def store_file(conn: sqlite3.Connection, filename: str, kind: str, content: bytes) -> Optional[int]:
    digest = sha256_bytes(content)
    now = datetime.utcnow().isoformat(timespec="seconds")
    try:
        cur = conn.execute(
            "INSERT INTO files(filename,kind,sha256,uploaded_at,content) VALUES(?,?,?,?,?)",
            (filename, kind, digest, now, content)
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # già presente


def iter_upload_payloads(uploaded) -> List[Tuple[str, bytes]]:
    """
    Normalizza:
    - file singolo (csv/xml/zip)
    - zip con più file
    Ritorna lista (filename, bytes)
    """
    payloads: List[Tuple[str, bytes]] = []
    if uploaded is None:
        return payloads

    raw = uploaded.getvalue()
    name = uploaded.name

    if name.lower().endswith(".zip"):
        zf = zipfile.ZipFile(io.BytesIO(raw))
        for info in zf.infolist():
            if info.is_dir():
                continue
            fn = os.path.basename(info.filename)
            if not fn:
                continue
            payloads.append((fn, zf.read(info.filename)))
    else:
        payloads.append((name, raw))
    return payloads


# -------------------------
# Parsing: Bank CSV
# -------------------------
BANK_TEMPLATE_COLUMNS = [
    "date", "value_date", "description", "amount", "currency", "balance",
    "counterparty_name", "counterparty_iban", "transaction_id"
]


def bank_template_csv_bytes() -> bytes:
    sample = pd.DataFrame([{
        "date": "2026-01-15",
        "value_date": "2026-01-15",
        "description": "BONIFICO DA Rossi Mario Fatt. 12/2026",
        "amount": "1234,56",
        "currency": "EUR",
        "balance": "10500,00",
        "counterparty_name": "ROSSI MARIO",
        "counterparty_iban": "IT00X0000000000000000000000",
        "transaction_id": "ABC12345"
    }], columns=BANK_TEMPLATE_COLUMNS)
    out = io.StringIO()
    sample.to_csv(out, index=False)
    return out.getvalue().encode("utf-8")


def sniff_read_csv(content: bytes) -> pd.DataFrame:
    text = content.decode("utf-8", errors="replace")
    # pandas con sep=None tenta di indovinare
    df = pd.read_csv(io.StringIO(text), sep=None, engine="python")
    return df


def map_bank_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c: norm_text(c) for c in df.columns}
    inv = {v: k for k, v in cols.items()}

    def pick(*cands):
        for c in cands:
            if c in inv:
                return inv[c]
        return None

    # riconosci varianti comuni
    c_date = pick("date", "data", "booking date", "datacontabile", "data contabile")
    c_vdate = pick("value_date", "valuta", "data valuta", "datavaluta")
    c_desc = pick("description", "descrizione", "causale", "descrizione operazione", "dettaglio")
    c_amt = pick("amount", "importo", "importo eur", "amount eur")
    c_bal = pick("balance", "saldo", "saldo contabile", "balance eur")

    c_cp_name = pick("counterparty_name", "beneficiario", "ordinante", "controparte", "contropartita", "name")
    c_cp_iban = pick("counterparty_iban", "iban controparte", "iban beneficiario", "iban ordinante", "iban")

    c_curr = pick("currency", "valuta conto", "ccy", "valuta")
    c_txid = pick("transaction_id", "id", "transaction id", "id transazione", "nr operazione", "reference")

    # supporto Dare/Avere
    c_dare = pick("dare", "addebito", "uscite", "debit")
    c_avere = pick("avere", "accredito", "entrate", "credit")

    out = pd.DataFrame()
    out["date"] = df[c_date] if c_date else None
    out["value_date"] = df[c_vdate] if c_vdate else df[c_date] if c_date else None
    out["description"] = df[c_desc] if c_desc else ""
    if c_amt:
        out["amount"] = df[c_amt]
    elif c_dare or c_avere:
        dare = df[c_dare] if c_dare else 0
        avere = df[c_avere] if c_avere else 0
        out["amount"] = pd.Series(avere).fillna(0) - pd.Series(dare).fillna(0)
    else:
        out["amount"] = None

    out["currency"] = df[c_curr] if c_curr else CURRENCY_DEFAULT
    out["balance"] = df[c_bal] if c_bal else None
    out["counterparty_name"] = df[c_cp_name] if c_cp_name else ""
    out["counterparty_iban"] = df[c_cp_iban] if c_cp_iban else ""
    out["transaction_id"] = df[c_txid] if c_txid else ""
    return out


def ingest_bank_csv(conn: sqlite3.Connection, file_id: int, filename: str, content: bytes) -> Dict[str, int]:
    df_raw = sniff_read_csv(content)
    df = map_bank_columns(df_raw)

    # pulizia formati
    df["tx_date"] = df["date"].apply(parse_date_any)
    df["value_date"] = df["value_date"].apply(parse_date_any)
    df["amount"] = df["amount"].apply(parse_decimal)
    df["balance"] = df["balance"].apply(parse_decimal)
    df["currency"] = df["currency"].fillna(CURRENCY_DEFAULT).astype(str).str.upper()

    inserted = 0
    skipped = 0
    inserted_hashes: List[str] = []
    now = datetime.utcnow().isoformat(timespec="seconds")

    # controlli coerenza per file: saldo progressivo (se disponibile)
    df2 = df.copy()
    df2["tx_date_dt"] = pd.to_datetime(df2["tx_date"], errors="coerce")
    df2 = df2.sort_values(["tx_date_dt", "value_date"], na_position="last")

    # recupera ultimo saldo registrato (per continuità inter-file)
    last = conn.execute(
        "SELECT tx_date, balance FROM bank_tx WHERE balance IS NOT NULL ORDER BY tx_date DESC, id DESC LIMIT 1"
    ).fetchone()
    last_balance = last["balance"] if last else None

    prev_balance = None
    first_balance = None

    for _, r in df2.iterrows():
        tx_date = r.get("tx_date")
        if tx_date is None:
            continue
        amount = r.get("amount")
        balance = r.get("balance")
        desc = str(r.get("description") or "")
        cp_name = str(r.get("counterparty_name") or "")
        cp_iban = str(r.get("counterparty_iban") or "")
        txid = str(r.get("transaction_id") or "")

        raw_json = json.dumps({
            "date": r.get("date"),
            "value_date": r.get("value_date"),
            "description": desc,
            "amount": r.get("amount"),
            "currency": r.get("currency"),
            "balance": r.get("balance"),
            "counterparty_name": cp_name,
            "counterparty_iban": cp_iban,
            "transaction_id": txid,
        }, ensure_ascii=False)

        # hash robusto: transaction_id se c'è, altrimenti firma
        sig = txid.strip() or f"{tx_date}|{amount}|{balance}|{norm_text(desc)}|{norm_text(cp_name)}|{cp_iban}"
        tx_hash = hashlib.sha256(sig.encode("utf-8")).hexdigest()

        try:
            conn.execute("""
                INSERT INTO bank_tx(
                    tx_hash, tx_date, value_date, description, amount, currency, balance,
                    counterparty_name, counterparty_iban, raw_json, file_id, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (tx_hash, tx_date, r.get("value_date"), desc, amount, r.get("currency"), balance,
                  cp_name, cp_iban, raw_json, file_id, now))
            inserted += 1
            inserted_hashes.append(tx_hash)
        except sqlite3.IntegrityError:
            skipped += 1
            continue

        # controlli saldo: intra-file
        if balance is not None and amount is not None:
            if first_balance is None:
                first_balance = balance
                # continuità inter-file: confronta col last_balance + amount? Non sempre possibile
                if last_balance is not None:
                    # se il file include movimenti già caricati, questo check potrebbe essere fuorviante: lo segnaliamo soft
                    pass

            if prev_balance is not None:
                expected = prev_balance + amount
                ok = 1 if money_eq(expected, balance, tol=0.05) else 0
                conn.execute("""
                    INSERT INTO bank_checks(file_id, tx_hash, check_type, expected, found, ok, note, created_at)
                    VALUES(?,?,?,?,?,?,?,?)
                """, (file_id, tx_hash, "RUNNING_BALANCE", expected, balance, ok,
                      "Saldo atteso = saldo precedente + importo", now))
            prev_balance = balance

    conn.commit()
    # Controlli saldo anche rispetto ai movimenti già presenti nel DB
    if inserted_hashes:
        add_db_balance_checks(conn, file_id, inserted_hashes)
    return {"inserted": inserted, "skipped": skipped}


def add_db_balance_checks(conn: sqlite3.Connection, file_id: int, tx_hashes: List[str]) -> None:
    """Controllo saldo confrontando ogni movimento inserito con il movimento precedente già registrato nel DB.
    È un controllo 'soft': segnala possibili buchi o salti di saldo, ma non blocca l'importazione.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")
    for h in tx_hashes:
        tx = conn.execute(
            "SELECT id, tx_date, amount, balance FROM bank_tx WHERE tx_hash=?",
            (h,)
        ).fetchone()
        if not tx:
            continue
        if tx["tx_date"] is None or tx["amount"] is None or tx["balance"] is None:
            continue

        prev = conn.execute(
            """SELECT id, tx_date, balance
                 FROM bank_tx
                 WHERE balance IS NOT NULL
                   AND (tx_date < ? OR (tx_date = ? AND id < ?))
                 ORDER BY tx_date DESC, id DESC
                 LIMIT 1""",
            (tx["tx_date"], tx["tx_date"], tx["id"])
        ).fetchone()

        if not prev or prev["balance"] is None:
            continue

        expected = float(prev["balance"]) + float(tx["amount"])
        found = float(tx["balance"])
        ok = 1 if money_eq(expected, found, tol=0.05) else 0

        conn.execute(
            """INSERT INTO bank_checks(file_id, tx_hash, check_type, expected, found, ok, note, created_at)
                 VALUES(?,?,?,?,?,?,?,?)""",
            (file_id, h, "DB_RUNNING_BALANCE", expected, found, ok,
             f"Saldo atteso = saldo precedente nel DB (id {prev['id']}) + importo", now)
        )

    conn.commit()


# -------------------------
# Parsing: FatturaPA XML
# -------------------------
def fatturapa_detect(content: bytes) -> bool:
    try:
        root = ET.fromstring(content)
        tag = root.tag.lower()
        return "fatturaelettronica" in tag or any("fatturaelettronica" in (c.tag.lower()) for c in list(root)[:3])
    except Exception:
        return False


def extract_text(root: ET.Element, path: str) -> str:
    # ElementTree wildcard namespace: { * } funziona su .find/.findtext
    try:
        return (root.findtext(path) or "").strip()
    except Exception:
        return ""


def ingest_fatturapa_xml(conn: sqlite3.Connection, file_id: int, filename: str, content: bytes) -> Dict[str, int]:
    inserted, skipped = 0, 0
    now = datetime.utcnow().isoformat(timespec="seconds")

    root = ET.fromstring(content)

    # dati documento
    inv_date = parse_date_any(extract_text(root, ".//{*}DatiGeneraliDocumento/{*}Data"))
    inv_num = extract_text(root, ".//{*}DatiGeneraliDocumento/{*}Numero")
    due_date = parse_date_any(extract_text(root, ".//{*}DatiPagamento//{*}DataScadenzaPagamento"))
    total = parse_decimal(extract_text(root, ".//{*}DatiGeneraliDocumento/{*}ImportoTotaleDocumento"))
    currency = (extract_text(root, ".//{*}DatiGeneraliDocumento/{*}Divisa") or CURRENCY_DEFAULT).upper()

    # anagrafiche
    ced_vat = extract_text(root, ".//{*}CedentePrestatore//{*}IdFiscaleIVA/{*}IdCodice")
    ces_vat = extract_text(root, ".//{*}CessionarioCommittente//{*}IdFiscaleIVA/{*}IdCodice")

    ced_name = extract_text(root, ".//{*}CedentePrestatore//{*}Anagrafica/{*}Denominazione") \
        or (extract_text(root, ".//{*}CedentePrestatore//{*}Anagrafica/{*}Nome") + " " +
            extract_text(root, ".//{*}CedentePrestatore//{*}Anagrafica/{*}Cognome")).strip()

    ces_name = extract_text(root, ".//{*}CessionarioCommittente//{*}Anagrafica/{*}Denominazione") \
        or (extract_text(root, ".//{*}CessionarioCommittente//{*}Anagrafica/{*}Nome") + " " +
            extract_text(root, ".//{*}CessionarioCommittente//{*}Anagrafica/{*}Cognome")).strip()

    # riferimento EndToEnd (se presente in causali pagamento / allegati) - best effort
    e2e = ""
    blob_txt = ET.tostring(root, encoding="utf-8", method="text").decode("utf-8", errors="ignore")
    m = re.search(r"ENDTOENDID[:\s]*([A-Za-z0-9\-_]{6,})", blob_txt, re.IGNORECASE)
    if m:
        e2e = m.group(1)

    # direzione basata su profilo utente (VAT/CF)
    my_vat = norm_text(db_get_profile(conn, "my_vat", ""))
    direction = "UNKNOWN"
    party_name = ""
    party_vat = ""

    if my_vat:
        if norm_text(ced_vat) == my_vat:
            direction = "EMESSA"
            party_name = ces_name
            party_vat = ces_vat
        elif norm_text(ces_vat) == my_vat:
            direction = "RICEVUTA"
            party_name = ced_name
            party_vat = ced_vat

    # fallback: se non capito, metti come parte il "cedente" (più utile come fornitore)
    if not party_name:
        party_name = ced_name or ces_name
        party_vat = ced_vat or ces_vat

    inv_hash = hashlib.sha256(
        f"{direction}|{inv_date}|{inv_num}|{total}|{norm_text(party_name)}|{party_vat}".encode("utf-8")
    ).hexdigest()

    try:
        conn.execute("""
            INSERT INTO invoices(
                inv_hash, direction, party_name, party_name_norm, party_vat,
                invoice_number, invoice_date, due_date, total_amount, currency, e2e_reference, raw_xml, file_id, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (inv_hash, direction, party_name, norm_text(party_name), party_vat,
              inv_num, inv_date, due_date, total, currency, e2e, content.decode("utf-8", errors="replace"), file_id, now))
        inserted += 1
    except sqlite3.IntegrityError:
        skipped += 1

    conn.commit()
    return {"inserted": inserted, "skipped": skipped}


# -------------------------
# Parsing: SEPA SDD pain.008 + esiti (pain.002 / camt.054 best effort)
# -------------------------
def sepa_detect(content: bytes) -> bool:
    try:
        root = ET.fromstring(content)
        txt = (root.tag or "").lower()
        return ("pain.008" in txt) or ("pain.002" in txt) or ("camt.054" in txt) or ("document" in txt)
    except Exception:
        return False


def ingest_sdd_pain008(conn: sqlite3.Connection, file_id: int, filename: str, content: bytes) -> Dict[str, int]:
    root = ET.fromstring(content)
    now = datetime.utcnow().isoformat(timespec="seconds")
    inserted, skipped = 0, 0

    msg_id = extract_text(root, ".//{*}GrpHdr/{*}MsgId")
    coll_dt = parse_date_any(extract_text(root, ".//{*}ReqdColltnDt"))
    creditor_name = extract_text(root, ".//{*}Cdtr/{*}Nm")
    creditor_id = extract_text(root, ".//{*}CdtrSchmeId//{*}Id//{*}Othr/{*}Id")

    for tx in root.findall(".//{*}DrctDbtTxInf"):
        debtor_name = (tx.findtext(".//{*}Dbtr/{*}Nm") or "").strip()
        debtor_iban = (tx.findtext(".//{*}DbtrAcct/{*}Id/{*}IBAN") or "").strip()
        mandate_id = (tx.findtext(".//{*}MndtRltdInf/{*}MndtId") or "").strip()
        e2e = (tx.findtext(".//{*}PmtId/{*}EndToEndId") or "").strip()
        amt = parse_decimal(tx.findtext(".//{*}InstdAmt"))
        ccy = (tx.find(".//{*}InstdAmt").attrib.get("Ccy") if tx.find(".//{*}InstdAmt") is not None else "") or CURRENCY_DEFAULT
        rem = " ".join([t.text.strip() for t in tx.findall(".//{*}RmtInf/{*}Ustrd") if t.text])

        sdd_hash = hashlib.sha256(
            f"{msg_id}|{coll_dt}|{e2e}|{mandate_id}|{amt}|{debtor_iban}|{norm_text(debtor_name)}".encode("utf-8")
        ).hexdigest()

        try:
            conn.execute("""
                INSERT INTO sdd(
                    sdd_hash, message_id, collection_date, creditor_name, creditor_id,
                    debtor_name, debtor_iban, mandate_id, end_to_end_id, amount, currency, remittance,
                    raw_xml, file_id, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (sdd_hash, msg_id, coll_dt, creditor_name, creditor_id,
                  debtor_name, debtor_iban, mandate_id, e2e, amt, str(ccy).upper(), rem,
                  content.decode("utf-8", errors="replace"), file_id, now))
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1

    conn.commit()
    return {"inserted": inserted, "skipped": skipped}


def ingest_esiti_xml(conn: sqlite3.Connection, file_id: int, filename: str, content: bytes) -> Dict[str, int]:
    root = ET.fromstring(content)
    tag = (root.tag or "").lower()
    now = datetime.utcnow().isoformat(timespec="seconds")
    inserted, skipped = 0, 0

    # pain.002: Transaction status report
    if "pain.002" in tag or root.find(".//{*}TxInfAndSts") is not None:
        for tx in root.findall(".//{*}TxInfAndSts"):
            e2e = (tx.findtext(".//{*}OrgnlEndToEndId") or "").strip()
            sts = (tx.findtext(".//{*}TxSts") or "").strip()
            rc = (tx.findtext(".//{*}StsRsnInf/{*}Rsn/{*}Cd") or "").strip()
            rt = " ".join([t.text.strip() for t in tx.findall(".//{*}StsRsnInf/{*}AddtlInf") if t.text])
            amt = parse_decimal(tx.findtext(".//{*}OrgnlTxRef//{*}Amt//{*}InstdAmt") or tx.findtext(".//{*}Amt"))
            vdt = parse_date_any(tx.findtext(".//{*}AccptncDtTm") or tx.findtext(".//{*}RltdDt") or "")

            out_hash = hashlib.sha256(f"{e2e}|{sts}|{rc}|{amt}|{vdt}".encode("utf-8")).hexdigest()
            try:
                conn.execute("""
                    INSERT INTO sdd_outcomes(
                        out_hash, end_to_end_id, status, reason_code, reason_text, value_date,
                        amount, currency, raw_xml, file_id, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """, (out_hash, e2e, sts, rc, rt, vdt, amt, CURRENCY_DEFAULT,
                      content.decode("utf-8", errors="replace"), file_id, now))
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1

    else:
        # camt.054 (best effort)
        for tx in root.findall(".//{*}TxDtls"):
            e2e = (tx.findtext(".//{*}Refs/{*}EndToEndId") or "").strip()
            amt = parse_decimal(tx.findtext(".//{*}Amt") or tx.findtext(".//{*}AmtDtls//{*}Amt") or "")
            vdt = parse_date_any(tx.findtext(".//{*}ValDt/{*}Dt") or tx.findtext(".//{*}BookgDt/{*}Dt") or "")
            rt = " ".join([t.text.strip() for t in tx.findall(".//{*}AddtlTxInf") if t.text])
            out_hash = hashlib.sha256(f"{e2e}|{amt}|{vdt}|{rt[:50]}".encode("utf-8")).hexdigest()

            try:
                conn.execute("""
                    INSERT INTO sdd_outcomes(
                        out_hash, end_to_end_id, status, reason_code, reason_text, value_date,
                        amount, currency, raw_xml, file_id, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """, (out_hash, e2e, "UNKNOWN", "", rt, vdt, amt, CURRENCY_DEFAULT,
                      content.decode("utf-8", errors="replace"), file_id, now))
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1

    conn.commit()
    return {"inserted": inserted, "skipped": skipped}


# -------------------------
# Alias / Canonical party
# -------------------------
def canonical_party(conn: sqlite3.Connection, name: str) -> str:
    n = norm_text(name)
    row = conn.execute("SELECT canonical FROM party_aliases WHERE alias_norm=?", (n,)).fetchone()
    return row["canonical"] if row else (name or "")


def upsert_alias(conn: sqlite3.Connection, alias: str, canonical: str) -> None:
    conn.execute("""
        INSERT INTO party_aliases(alias_norm, canonical) VALUES(?,?)
        ON CONFLICT(alias_norm) DO UPDATE SET canonical=excluded.canonical
    """, (norm_text(alias), canonical))
    conn.commit()


# -------------------------
# Riconciliazione: candidati + scoring
# -------------------------
def get_open_invoices(conn: sqlite3.Connection, direction: str) -> List[sqlite3.Row]:
    # invoice "aperta" = totale - allocazioni confermate > 0
    rows = conn.execute("""
        SELECT i.*,
               COALESCE(SUM(m.allocated_amount),0) AS allocated
        FROM invoices i
        LEFT JOIN matches m
            ON m.doc_type='INVOICE' AND m.doc_id=i.id AND m.status='CONFIRMED'
        WHERE i.direction=?
        GROUP BY i.id
        HAVING (COALESCE(i.total_amount,0) - COALESCE(SUM(m.allocated_amount),0)) > 0.0001
        ORDER BY i.invoice_date DESC
    """, (direction,)).fetchall()
    return rows


def tx_already_handled(conn: sqlite3.Connection, tx_id: int) -> bool:
    row = conn.execute("""
        SELECT 1 FROM matches WHERE bank_tx_id=? AND status IN ('CONFIRMED','IGNORED') LIMIT 1
    """, (tx_id,)).fetchone()
    return bool(row)


def score_candidate(conn: sqlite3.Connection, tx: sqlite3.Row, doc: sqlite3.Row, doc_type: str) -> Tuple[int, str]:
    """
    Ritorna (confidence 0-100, label)
    label: GREEN / YELLOW
    """
    tx_amt = tx["amount"]
    tx_date = tx["tx_date"]
    desc = tx["description"] or ""
    # Applica alias/canonical se presenti, altrimenti usa il nome originale
    cp_name = canonical_party(conn, tx["counterparty_name"] or "")
    cp_raw = tx["counterparty_name"] or ""

    # amount expected
    if doc_type == "INVOICE":
        inv_amt = doc["total_amount"]
        needed = abs(tx_amt) if tx_amt is not None else None
        amt_ok = money_eq(inv_amt, needed, tol=0.05)
        amt_diff = abs((inv_amt or 0) - (needed or 0))
        party_sim = similarity(doc["party_name"], cp_name or cp_raw or desc)
        date_sim = 0
        if tx_date and doc["invoice_date"]:
            try:
                d1 = pd.to_datetime(tx_date)
                d2 = pd.to_datetime(doc["invoice_date"])
                dd = abs((d1 - d2).days)
                date_sim = max(0, 100 - int(dd * 7))
            except Exception:
                date_sim = 0

        conf = int(round(0.55 * party_sim + 0.35 * date_sim + 0.10 * (100 if amt_ok else max(0, 100 - int(amt_diff * 10)))))
        # extra boost se numero fattura nel testo
        inv_num = (doc["invoice_number"] or "").strip()
        if inv_num and re.search(rf"\b{re.escape(inv_num)}\b", desc):
            conf = min(100, conf + 10)
        label = "GREEN" if (amt_ok and party_sim >= 85) else "YELLOW"
        return conf, label

    elif doc_type == "SDD":
        needed = abs(tx_amt) if tx_amt is not None else None
        amt_ok = money_eq(doc["amount"], needed, tol=0.05)
        party_sim = similarity(doc["debtor_name"], cp_name or cp_raw or desc)
        conf = int(round(0.65 * party_sim + 0.35 * (100 if amt_ok else 60)))
        label = "GREEN" if (amt_ok and party_sim >= 85) else "YELLOW"
        return conf, label

    elif doc_type == "SDD_OUTCOME":
        # outcomes spesso non hanno controparte; score su EndToEnd e importo
        needed = abs(tx_amt) if tx_amt is not None else None
        amt_ok = money_eq(doc["amount"], needed, tol=0.05) if doc["amount"] is not None else True
        conf = 85 if amt_ok else 60
        label = "GREEN" if amt_ok else "YELLOW"
        return conf, label

    return 0, "YELLOW"



def amount_close(a: Optional[float], b: Optional[float], abs_tol: float = 5.0, pct_tol: float = 0.01) -> bool:
    """Confronto importi con tolleranza assoluta e percentuale (utile su commissioni/arrotondamenti)."""
    if a is None or b is None:
        return False
    return abs(a - b) <= max(abs_tol, abs(b) * pct_tol)


def candidate_docs_for_tx(conn: sqlite3.Connection, tx: sqlite3.Row, topn: int = 8) -> List[Dict]:
    """Genera candidati ordinati per confidence (fatture / SDD / esiti)."""
    if tx["amount"] is None:
        return []
    incoming = tx["amount"] > 0
    direction = "EMESSA" if incoming else "RICEVUTA"

    cands: List[Dict] = []
    needed = abs(tx["amount"])

    def add_invoice_candidates(abs_tol: float, pct_tol: float) -> None:
        invs = get_open_invoices(conn, direction)
        for inv in invs:
            if inv["total_amount"] is None:
                continue
            inv_amt = float(inv["total_amount"])
            if not amount_close(inv_amt, needed, abs_tol=abs_tol, pct_tol=pct_tol):
                # se il numero fattura è in descrizione, allarghiamo un po' l'importo
                inv_num = (inv["invoice_number"] or "").strip()
                if not inv_num or not re.search(rf"{re.escape(inv_num)}", (tx['description'] or '')):
                    continue

            conf, label = score_candidate(conn, tx, inv, "INVOICE")
            if conf >= 55:
                cands.append({
                    "doc_type": "INVOICE",
                    "doc_id": inv["id"],
                    "confidence": conf,
                    "label": label,
                    "title": f"Fattura {inv['invoice_number']} ({inv['invoice_date']}) - {inv['party_name']} - {float(inv['total_amount'] or 0):.2f} {inv['currency']}"
                })

    def add_sdd_candidates(abs_tol: float, pct_tol: float) -> None:
        if incoming:
            return
        rows = conn.execute("SELECT * FROM sdd ORDER BY collection_date DESC, id DESC").fetchall()
        for sdd in rows:
            if sdd["amount"] is None:
                continue
            sdd_amt = float(sdd["amount"])
            if not amount_close(sdd_amt, needed, abs_tol=abs_tol, pct_tol=pct_tol):
                continue
            conf, label = score_candidate(conn, tx, sdd, "SDD")
            if conf >= 55:
                cands.append({
                    "doc_type": "SDD",
                    "doc_id": sdd["id"],
                    "confidence": conf,
                    "label": label,
                    "title": f"SDD {sdd['end_to_end_id'] or ''} - {sdd['debtor_name']} - {float(sdd['amount'] or 0):.2f} {sdd['currency']} (coll. {sdd['collection_date']})"
                })

    # pass 1: tolleranza stretta
    add_invoice_candidates(abs_tol=5.0, pct_tol=0.01)
    add_sdd_candidates(abs_tol=5.0, pct_tol=0.01)

    # pass 2: se non trova nulla, allarga (commissioni, arrotondamenti, split)
    if not cands:
        add_invoice_candidates(abs_tol=15.0, pct_tol=0.03)
        add_sdd_candidates(abs_tol=15.0, pct_tol=0.03)

    # Outcomes per EndToEnd presente in descrizione (best effort)
    desc = tx["description"] or ""
    m = re.search(r"([A-Za-z0-9\-_]{10,})", desc)
    if m:
        e2e = m.group(1)
        outs = conn.execute("SELECT * FROM sdd_outcomes WHERE end_to_end_id=? ORDER BY id DESC", (e2e,)).fetchall()
        for o in outs:
            conf, label = score_candidate(conn, tx, o, "SDD_OUTCOME")
            if conf >= 55:
                cands.append({
                    "doc_type": "SDD_OUTCOME",
                    "doc_id": o["id"],
                    "confidence": conf,
                    "label": label,
                    "title": f"Esito SDD {o['end_to_end_id']} - {o['status']} {o['reason_code']} - {float(o['amount'] or 0):.2f} {o['currency']}"
                })

    cands.sort(key=lambda x: x["confidence"], reverse=True)
    return cands[:topn]


def best_status_emoji(cands: List[Dict]) -> str:
    if not cands:
        return "⚪"
    best = cands[0]
    return "🟢" if best["label"] == "GREEN" else "🟡"


def confirm_match(conn: sqlite3.Connection, tx_id: int, doc_type: str, doc_id: int,
                  confidence: int, allocated_amount: float, note: str = "") -> None:
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute("""
        INSERT INTO matches(bank_tx_id, doc_type, doc_id, confidence, status, allocated_amount, note, created_at)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(bank_tx_id, doc_type, doc_id) DO UPDATE SET
            confidence=excluded.confidence,
            status=excluded.status,
            allocated_amount=excluded.allocated_amount,
            note=excluded.note
    """, (tx_id, doc_type, doc_id, confidence, "CONFIRMED", allocated_amount, note, now))
    conn.commit()


def ignore_tx(conn: sqlite3.Connection, tx_id: int, note: str = "") -> None:
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute("""
        INSERT INTO matches(bank_tx_id, doc_type, doc_id, confidence, status, allocated_amount, note, created_at)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(bank_tx_id, doc_type, doc_id) DO UPDATE SET
            status=excluded.status,
            note=excluded.note
    """, (tx_id, "NONE", 0, 0, "IGNORED", 0.0, note, now))
    conn.commit()


# -------------------------
# Suggerimenti automatici (match "verdi")
# -------------------------
def suggest_match(conn: sqlite3.Connection, tx_id: int, doc_type: str, doc_id: int,
                  confidence: int, allocated_amount: float, note: str = "auto") -> None:
    """Crea/aggiorna un suggerimento (status=SUGGESTED) per un movimento non ancora gestito."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    # elimina suggerimenti precedenti per lo stesso movimento (non tocca i CONFIRMED/IGNORED)
    conn.execute("DELETE FROM matches WHERE bank_tx_id=? AND status='SUGGESTED'", (tx_id,))
    conn.execute(
        """INSERT INTO matches(bank_tx_id, doc_type, doc_id, confidence, status, allocated_amount, note, created_at)
             VALUES(?,?,?,?,?,?,?,?)""",
        (tx_id, doc_type, doc_id, confidence, "SUGGESTED", allocated_amount, note, now)
    )
    conn.commit()


def generate_auto_suggestions(conn: sqlite3.Connection, scan_limit: int = 500, min_conf: int = 90) -> Dict[str, int]:
    """Scansiona movimenti aperti e crea suggerimenti per quelli con match molto probabile."""
    rows = conn.execute(
        """SELECT * FROM bank_tx
             WHERE id NOT IN (SELECT bank_tx_id FROM matches WHERE status IN ('CONFIRMED','IGNORED'))
             ORDER BY tx_date DESC, id DESC
             LIMIT ?""",
        (scan_limit,)
    ).fetchall()

    suggested = 0
    evaluated = 0
    for tx in rows:
        evaluated += 1
        cands = candidate_docs_for_tx(conn, tx, topn=1)
        if not cands:
            continue
        best = cands[0]
        if best["label"] == "GREEN" and best["confidence"] >= min_conf:
            suggest_match(conn, int(tx["id"]), best["doc_type"], int(best["doc_id"]),
                          int(best["confidence"]), float(abs(tx["amount"] or 0)), note="auto")
            suggested += 1

    return {"evaluated": evaluated, "suggested": suggested}


def doc_title(conn: sqlite3.Connection, doc_type: str, doc_id: int) -> str:
    if doc_type == "INVOICE":
        r = conn.execute("SELECT * FROM invoices WHERE id=?", (doc_id,)).fetchone()
        if not r:
            return "Fattura (non trovata)"
        return f"Fattura {r['invoice_number']} ({r['invoice_date']}) - {r['party_name']} - {float(r['total_amount'] or 0):.2f} {r['currency']}"
    if doc_type == "SDD":
        r = conn.execute("SELECT * FROM sdd WHERE id=?", (doc_id,)).fetchone()
        if not r:
            return "SDD (non trovato)"
        return f"SDD {r['end_to_end_id'] or ''} - {r['debtor_name']} - {float(r['amount'] or 0):.2f} {r['currency']} (coll. {r['collection_date']})"
    if doc_type == "SDD_OUTCOME":
        r = conn.execute("SELECT * FROM sdd_outcomes WHERE id=?", (doc_id,)).fetchone()
        if not r:
            return "Esito SDD (non trovato)"
        return f"Esito {r['end_to_end_id']} - {r['status']} {r['reason_code']} - {float(r['amount'] or 0):.2f} {r['currency']}"
    return f"{doc_type} #{doc_id}"


# -------------------------
# Export: Excel / PDF
# -------------------------
def export_excel_bytes(conn: sqlite3.Connection) -> bytes:
    tx = pd.read_sql_query("SELECT * FROM bank_tx ORDER BY tx_date DESC, id DESC", conn)
    inv = pd.read_sql_query("SELECT * FROM invoices ORDER BY invoice_date DESC, id DESC", conn)
    sdd = pd.read_sql_query("SELECT * FROM sdd ORDER BY collection_date DESC, id DESC", conn)
    out = pd.read_sql_query("SELECT * FROM sdd_outcomes ORDER BY value_date DESC, id DESC", conn)
    m = pd.read_sql_query("SELECT * FROM matches ORDER BY created_at DESC, id DESC", conn)
    chk = pd.read_sql_query("SELECT * FROM bank_checks ORDER BY created_at DESC, id DESC", conn)

    parties = parties_summary_df(conn)

    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        tx.to_excel(writer, index=False, sheet_name="BankTransactions")
        inv.to_excel(writer, index=False, sheet_name="Invoices")
        sdd.to_excel(writer, index=False, sheet_name="SDD")
        out.to_excel(writer, index=False, sheet_name="SDD_Outcomes")
        m.to_excel(writer, index=False, sheet_name="Matches")
        chk.to_excel(writer, index=False, sheet_name="Checks")
        parties.to_excel(writer, index=False, sheet_name="PartiesSummary")
    return bio.getvalue()



def parties_summary_df(conn: sqlite3.Connection) -> pd.DataFrame:
    """Situazione per controparte (clienti/fornitori) basata su fatture e match confermati.
    - direction=EMESSA  => CLIENTE (crediti: Dare)
    - direction=RICEVUTA => FORNITORE (debiti: Avere)
    """
    inv = pd.read_sql_query(
        """SELECT id, direction, party_name, party_vat, invoice_number, invoice_date, total_amount, currency
             FROM invoices""",
        conn
    )

    base_cols = [
        "categoria", "party_name", "party_vat", "direction",
        "invoiced", "incassato", "pagato", "outstanding",
        "saldo_dare", "saldo_avere", "n_invoices"
    ]

    if inv.empty:
        return pd.DataFrame(columns=base_cols)

    paid = pd.read_sql_query(
        """SELECT m.doc_id AS invoice_id, SUM(m.allocated_amount) AS paid
             FROM matches m
             WHERE m.doc_type='INVOICE' AND m.status='CONFIRMED'
             GROUP BY m.doc_id""",
        conn
    )

    inv = inv.merge(paid, how="left", left_on="id", right_on="invoice_id")
    inv["paid"] = inv["paid"].fillna(0.0)
    inv["total_amount"] = inv["total_amount"].fillna(0.0)
    inv["outstanding"] = (inv["total_amount"] - inv["paid"]).clip(lower=0.0)

    g = inv.groupby(["party_name", "party_vat", "direction"], dropna=False).agg(
        invoiced=("total_amount", "sum"),
        paid=("paid", "sum"),
        outstanding=("outstanding", "sum"),
        n_invoices=("id", "count")
    ).reset_index()

    g["categoria"] = g["direction"].map({"EMESSA": "CLIENTE", "RICEVUTA": "FORNITORE"}).fillna("ALTRO")
    g["incassato"] = g.apply(lambda r: r["paid"] if r["direction"] == "EMESSA" else 0.0, axis=1)
    g["pagato"] = g.apply(lambda r: r["paid"] if r["direction"] == "RICEVUTA" else 0.0, axis=1)
    g["saldo_dare"] = g.apply(lambda r: r["outstanding"] if r["direction"] == "EMESSA" else 0.0, axis=1)
    g["saldo_avere"] = g.apply(lambda r: r["outstanding"] if r["direction"] == "RICEVUTA" else 0.0, axis=1)

    # riordino colonne
    g = g[[
        "categoria", "party_name", "party_vat", "direction",
        "invoiced", "incassato", "pagato", "outstanding",
        "saldo_dare", "saldo_avere", "n_invoices"
    ]]

    g = g.sort_values(["outstanding", "invoiced"], ascending=[False, False])
    return g



def export_pdf_bytes(conn: sqlite3.Connection) -> bytes:
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph(APP_TITLE, styles["Title"]))
    story.append(Paragraph(f"Report generato: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
    story.append(Spacer(1, 12))

    parties = parties_summary_df(conn)
    
    story.append(Paragraph("Situazione Clienti/Fornitori (sintesi)", styles["Heading2"]))
    if parties.empty:
        story.append(Paragraph("Nessun dato disponibile.", styles["Normal"]))
    else:
        table_data = [["Categoria", "Parte", "P.IVA", "Fatturato", "Inc./Pag.", "Residuo", "Dare/Avere", "N."]]
        for _, r in parties.head(30).iterrows():
            mov = float(r.get("incassato", 0) or 0) + float(r.get("pagato", 0) or 0)
            da = "Dare" if float(r.get("saldo_dare", 0) or 0) > 0 else ("Avere" if float(r.get("saldo_avere", 0) or 0) > 0 else "-")
            table_data.append([
                r.get("categoria", "") or "",
                r.get("party_name", "") or "",
                r.get("party_vat", "") or "",
                f"{float(r.get('invoiced', 0) or 0):.2f}",
                f"{mov:.2f}",
                f"{float(r.get('outstanding', 0) or 0):.2f}",
                da,
                str(int(r.get("n_invoices", 0) or 0))
            ])
        t = Table(table_data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (3, 1), (5, -1), "RIGHT"),
        ]))
        story.append(t)

    story.append(PageBreak())

    # Anomalie saldo
    chk = pd.read_sql_query("""
        SELECT * FROM bank_checks WHERE ok=0 ORDER BY created_at DESC LIMIT 50
    """, conn)
    story.append(Paragraph("Anomalie Controlli Saldo (ultime 50)", styles["Heading2"]))
    if chk.empty:
        story.append(Paragraph("Nessuna anomalia rilevata.", styles["Normal"]))
    else:
        table_data = [["File", "Tx", "Check", "Atteso", "Trovato"]]
        for _, r in chk.iterrows():
            table_data.append([str(r["file_id"]), r["tx_hash"][:10], r["check_type"],
                               f"{r['expected']:.2f}" if pd.notna(r["expected"]) else "",
                               f"{r['found']:.2f}" if pd.notna(r["found"]) else ""])
        t = Table(table_data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]))
        story.append(t)

    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=A4, rightMargin=18, leftMargin=18, topMargin=18, bottomMargin=18)
    doc.build(story)
    return bio.getvalue()


# -------------------------
# UI
# -------------------------
def sidebar_profile(conn: sqlite3.Connection) -> None:
    st.sidebar.header("Impostazioni")
    my_vat = st.sidebar.text_input("Tua P.IVA (usata per distinguere Emesse/Ricevute)", value=db_get_profile(conn, "my_vat", ""))
    my_name = st.sidebar.text_input("Tuo Nome/Ragione Sociale (facoltativo)", value=db_get_profile(conn, "my_name", ""))
    if st.sidebar.button("Salva impostazioni"):
        db_set_profile(conn, "my_vat", my_vat.strip())
        db_set_profile(conn, "my_name", my_name.strip())
        st.sidebar.success("Salvato.")


def ui_uploads(conn: sqlite3.Connection) -> None:
    st.subheader("Caricamenti")

    # template bank CSV
    st.download_button(
        "⬇️ Scarica template CSV estratto conto",
        data=bank_template_csv_bytes(),
        file_name="template_estratto_conto.csv",
        mime="text/csv"
    )
    st.caption("Usa il template per garantire importazione senza sorprese (date e importi vengono comunque normalizzati).")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 1) Estratto conto bancario (CSV)")
        bank_files = st.file_uploader("Carica CSV estratto conto", type=["csv", "zip"], accept_multiple_files=True, key="bank_up")
        if bank_files:
            for up in bank_files:
                for fn, payload in iter_upload_payloads(up):
                    fid = store_file(conn, fn, "BANK_CSV", payload)
                    if fid is None:
                        st.info(f"⏭️ {fn}: già caricato (saltato).")
                        continue
                    try:
                        res = ingest_bank_csv(conn, fid, fn, payload)
                        st.success(f"✅ {fn}: movimenti inseriti {res['inserted']} (saltati {res['skipped']}).")
                    except Exception as e:
                        st.error(f"❌ {fn}: errore importazione CSV: {e}")

    with col2:
        st.markdown("### 2) Documenti (XML/ZIP)")
        doc_files = st.file_uploader("Carica FatturePA / SEPA SDD / Esiti (XML o ZIP)", type=["xml", "zip"], accept_multiple_files=True, key="docs_up")
        if doc_files:
            for up in doc_files:
                for fn, payload in iter_upload_payloads(up):
                    kind = "UNKNOWN"
                    if fn.lower().endswith(".xml") and fatturapa_detect(payload):
                        kind = "FATTURAPA_XML"
                    elif fn.lower().endswith(".xml") and sepa_detect(payload):
                        kind = "SEPA_XML"

                    fid = store_file(conn, fn, kind, payload)
                    if fid is None:
                        st.info(f"⏭️ {fn}: già caricato (saltato).")
                        continue

                    try:
                        if kind == "FATTURAPA_XML":
                            res = ingest_fatturapa_xml(conn, fid, fn, payload)
                            st.success(f"✅ {fn}: fatture inserite {res['inserted']} (saltate {res['skipped']}).")
                        elif kind == "SEPA_XML":
                            # decidi se pain.008 o esiti
                            root = ET.fromstring(payload)
                            tag = (root.tag or "").lower()
                            if "pain.008" in tag or root.find(".//{*}DrctDbtTxInf") is not None:
                                res = ingest_sdd_pain008(conn, fid, fn, payload)
                                st.success(f"✅ {fn}: SDD inseriti {res['inserted']} (saltati {res['skipped']}).")
                            else:
                                res = ingest_esiti_xml(conn, fid, fn, payload)
                                st.success(f"✅ {fn}: esiti inseriti {res['inserted']} (saltati {res['skipped']}).")
                        else:
                            st.warning(f"⚠️ {fn}: tipo file non riconosciuto (caricato e archiviato nel DB, ma non parsato).")
                    except Exception as e:
                        st.error(f"❌ {fn}: errore parsing XML: {e}")


def ui_checks(conn: sqlite3.Connection) -> None:
    st.subheader("Controlli coerenza saldi")
    df = pd.read_sql_query("""
        SELECT bc.created_at, f.filename, bc.tx_hash, bc.check_type, bc.expected, bc.found, bc.ok, bc.note
        FROM bank_checks bc
        LEFT JOIN files f ON f.id=bc.file_id
        ORDER BY bc.created_at DESC
        LIMIT 500
    """, conn)
    if df.empty:
        st.info("Nessun controllo registrato (serve un estratto conto con colonna saldo).")
        return
    st.dataframe(df, use_container_width=True)
    anomalies = df[df["ok"] == 0]
    if not anomalies.empty:
        st.warning(f"Anomalie rilevate: {len(anomalies)} (potrebbero dipendere da movimenti già presenti o da saldi non contabili).")


def ui_aliases(conn: sqlite3.Connection) -> None:
    st.subheader("Alias / Normalizzazione nominativi")
    st.caption("Serve per dire all'app che 'ROSSI MARIO' e 'Rossi M.' sono la stessa controparte.")
    c1, c2 = st.columns(2)
    with c1:
        alias = st.text_input("Alias (come appare in banca o in fattura)")
    with c2:
        canonical = st.text_input("Nome canonico (come vuoi vederlo)", value="")
    if st.button("Salva alias"):
        if alias.strip() and canonical.strip():
            upsert_alias(conn, alias, canonical)
            st.success("Alias salvato.")
        else:
            st.error("Inserisci sia alias che nome canonico.")

    df = pd.read_sql_query("SELECT alias_norm, canonical FROM party_aliases ORDER BY canonical", conn)
    st.dataframe(df, use_container_width=True)



def ui_reconciliation(conn: sqlite3.Connection) -> None:
    st.subheader("Riconciliazione")
    st.caption("✅ = già confermato/ignorato • 🤖 = suggerimento automatico • 🟢 = match molto probabile • 🟡 = da confermare • ⚪ = nessun candidato")

    # --- Suggerimenti automatici (match verdi) ---
    with st.expander("Suggerimenti automatici (match 'verdi')", expanded=False):
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            scan_limit = st.number_input("Movimenti da analizzare", min_value=50, max_value=5000, value=400, step=50, key="scan_limit")
        with c2:
            min_conf = st.slider("Soglia confidenza", min_value=70, max_value=100, value=90, step=1, key="min_conf")
        with c3:
            if st.button("🤖 Genera / aggiorna suggerimenti", key="gen_sug"):
                res = generate_auto_suggestions(conn, scan_limit=int(scan_limit), min_conf=int(min_conf))
                st.success(f"Suggerimenti creati/aggiornati: {res['suggested']} (movimenti valutati: {res['evaluated']}).")

        sug = pd.read_sql_query(
            """SELECT b.id AS bank_tx_id, b.tx_date, b.amount, b.counterparty_name, b.description,
                      m.doc_type, m.doc_id, m.confidence, m.allocated_amount, m.created_at
               FROM matches m
               JOIN bank_tx b ON b.id=m.bank_tx_id
               WHERE m.status='SUGGESTED'
               ORDER BY m.confidence DESC, m.created_at DESC
               LIMIT 500""",
            conn
        )

        if sug.empty:
            st.info("Nessun suggerimento presente (genera sopra, oppure carica più documenti).")
        else:
            # arricchisci con titolo documento
            titles = []
            for _, r in sug.iterrows():
                try:
                    titles.append(doc_title(conn, str(r["doc_type"]), int(r["doc_id"])))
                except Exception:
                    titles.append(f"{r['doc_type']} #{r['doc_id']}")
            sug["documento"] = titles
            sug["conferma"] = False
            sug["scarta"] = False

            view = sug[["conferma", "scarta", "bank_tx_id", "tx_date", "amount", "counterparty_name", "documento", "confidence"]]
            edited = st.data_editor(view, use_container_width=True, hide_index=True, num_rows="fixed")

            c4, c5 = st.columns(2)
            with c4:
                if st.button("✅ Conferma selezionati", key="confirm_sug"):
                    n = 0
                    for _, r in edited[edited["conferma"] == True].iterrows():
                        tx_id = int(r["bank_tx_id"])
                        # recupera match suggerito completo
                        mrow = conn.execute(
                            "SELECT * FROM matches WHERE bank_tx_id=? AND status='SUGGESTED' ORDER BY confidence DESC LIMIT 1",
                            (tx_id,)
                        ).fetchone()
                        if not mrow:
                            continue
                        confirm_match(conn, tx_id, mrow["doc_type"], int(mrow["doc_id"]),
                                      int(mrow["confidence"]), float(mrow["allocated_amount"] or abs(float(r["amount"] or 0))), note="auto-confirm")
                        n += 1
                    st.success(f"Confermati: {n}")
            with c5:
                if st.button("🧹 Scarta selezionati", key="discard_sug"):
                    n = 0
                    for _, r in edited[edited["scarta"] == True].iterrows():
                        conn.execute("DELETE FROM matches WHERE bank_tx_id=? AND status='SUGGESTED'", (int(r["bank_tx_id"]),))
                        n += 1
                    conn.commit()
                    st.warning(f"Scartati: {n}")

    # --- Lista movimenti ---
    colf1, colf2, colf3 = st.columns([1, 1, 1])
    with colf1:
        only_open = st.checkbox("Solo non riconciliati", value=True)
    with colf2:
        limit = st.number_input("Limite movimenti mostrati", min_value=50, max_value=5000, value=400, step=50)
    with colf3:
        direction_filter = st.selectbox("Tipo movimenti", ["Tutti", "Entrate", "Uscite"], index=0)

    df = pd.read_sql_query("SELECT * FROM bank_tx ORDER BY tx_date DESC, id DESC", conn)
    if df.empty:
        st.info("Nessun movimento bancario caricato.")
        return

    handled_ids = set([r["bank_tx_id"] for r in conn.execute(
        "SELECT DISTINCT bank_tx_id FROM matches WHERE status IN ('CONFIRMED','IGNORED')"
    ).fetchall()])

    sug_map = {}
    for r in conn.execute(
        "SELECT bank_tx_id, confidence FROM matches WHERE status='SUGGESTED'"
    ).fetchall():
        sug_map[int(r["bank_tx_id"])] = int(r["confidence"] or 0)

    if only_open:
        df = df[~df["id"].isin(list(handled_ids))]

    if direction_filter == "Entrate":
        df = df[df["amount"] > 0]
    elif direction_filter == "Uscite":
        df = df[df["amount"] < 0]

    df = df.head(int(limit)).copy()
    if df.empty:
        st.info("Nessun movimento da mostrare con i filtri correnti.")
        return

    # stato + best candidate (best effort)
    statuses = []
    best_summ = []
    for _, r in df.iterrows():
        tx_id = int(r["id"])
        if tx_id in handled_ids:
            statuses.append("✅")
            best_summ.append("Già confermato/ignorato")
            continue
        if tx_id in sug_map:
            statuses.append("🤖🟢" if sug_map[tx_id] >= 90 else "🤖🟡")
            # titolo del suggerimento
            mrow = conn.execute(
                "SELECT doc_type, doc_id FROM matches WHERE bank_tx_id=? AND status='SUGGESTED' ORDER BY confidence DESC LIMIT 1",
                (tx_id,)
            ).fetchone()
            best_summ.append(doc_title(conn, mrow["doc_type"], int(mrow["doc_id"])) if mrow else "")
            continue

        tx_row = conn.execute("SELECT * FROM bank_tx WHERE id=?", (tx_id,)).fetchone()
        cands = candidate_docs_for_tx(conn, tx_row, topn=1)
        statuses.append(best_status_emoji(cands))
        best_summ.append(cands[0]["title"] if cands else "")

    df_show = df[["id", "tx_date", "value_date", "amount", "currency", "balance", "counterparty_name", "description"]].copy()
    df_show.insert(0, "stato", statuses)
    df_show["miglior candidato"] = best_summ
    st.dataframe(df_show, use_container_width=True, hide_index=True)

    # selezione movimento (in base alla lista corrente)
    ids = df_show["id"].tolist()

    def _fmt(tx_id: int) -> str:
        row = df_show[df_show["id"] == tx_id].iloc[0]
        return f"{row['stato']}  ID {tx_id} • {row['tx_date']} • {float(row['amount'] or 0):.2f} {row['currency']} • {row['counterparty_name'] or ''}"

    sel_id = st.selectbox("Seleziona movimento da analizzare", ids, format_func=_fmt, index=0)
    tx = conn.execute("SELECT * FROM bank_tx WHERE id=?", (int(sel_id),)).fetchone()
    if not tx:
        return

    st.markdown("### Dettaglio movimento")
    st.write({
        "ID": tx["id"],
        "Data": tx["tx_date"],
        "Valuta": tx["value_date"],
        "Importo": tx["amount"],
        "Saldo": tx["balance"],
        "Controparte": tx["counterparty_name"],
        "IBAN": tx["counterparty_iban"],
        "Descrizione": tx["description"],
    })

    # correzioni movimento
    with st.expander("✏️ Correggi dati movimento (storicizzato via raw_json)", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            new_date = st.text_input("Data (YYYY-MM-DD)", value=str(tx["tx_date"] or ""), key=f"ed_dt_{sel_id}")
            new_vdate = st.text_input("Valuta (YYYY-MM-DD)", value=str(tx["value_date"] or ""), key=f"ed_vdt_{sel_id}")
        with c2:
            new_amount = st.text_input("Importo", value=str(tx["amount"] if tx["amount"] is not None else ""), key=f"ed_amt_{sel_id}")
            new_balance = st.text_input("Saldo (facoltativo)", value=str(tx["balance"] if tx["balance"] is not None else ""), key=f"ed_bal_{sel_id}")
        with c3:
            new_curr = st.text_input("Valuta (EUR)", value=str(tx["currency"] or "EUR"), key=f"ed_ccy_{sel_id}")
            new_iban = st.text_input("IBAN controparte", value=str(tx["counterparty_iban"] or ""), key=f"ed_iban_{sel_id}")

        new_cp = st.text_input("Nome controparte", value=str(tx["counterparty_name"] or ""), key=f"ed_cp_{sel_id}")
        new_desc = st.text_area("Descrizione", value=str(tx["description"] or ""), key=f"ed_desc_{sel_id}")

        if st.button("Salva modifiche movimento", key=f"save_tx_{sel_id}"):
            tx_date = parse_date_any(new_date)
            v_date = parse_date_any(new_vdate)
            amt = parse_decimal(new_amount)
            bal = parse_decimal(new_balance)
            ccy = (new_curr or "EUR").upper().strip()

            raw_json = json.dumps({
                "date": tx_date,
                "value_date": v_date,
                "description": new_desc,
                "amount": amt,
                "currency": ccy,
                "balance": bal,
                "counterparty_name": new_cp,
                "counterparty_iban": new_iban,
                "transaction_id": ""  # non ricostruibile qui
            }, ensure_ascii=False)

            conn.execute(
                """UPDATE bank_tx
                   SET tx_date=?, value_date=?, description=?, amount=?, currency=?, balance=?,
                       counterparty_name=?, counterparty_iban=?, raw_json=?
                   WHERE id=?""",
                (tx_date, v_date, new_desc, amt, ccy, bal, new_cp, new_iban, raw_json, int(sel_id))
            )
            conn.commit()
            st.success("Movimento aggiornato. (I match possono cambiare: riesegui la ricerca/candidati.)")

    # se già gestito
    if tx_already_handled(conn, int(sel_id)):
        st.success("Questo movimento risulta già gestito (CONFERMATO o IGNORATO).")
        if st.button("Rimuovi match/ignorato (solo per correggere)", key=f"del_match_{sel_id}"):
            conn.execute("DELETE FROM matches WHERE bank_tx_id=?", (int(sel_id),))
            conn.commit()
            st.warning("Match rimossi.")
        return

    # suggerimento presente per questo movimento?
    sug = conn.execute(
        "SELECT * FROM matches WHERE bank_tx_id=? AND status='SUGGESTED' ORDER BY confidence DESC LIMIT 1",
        (int(sel_id),)
    ).fetchone()
    if sug:
        st.info(f"🤖 Suggerimento: {doc_title(conn, sug['doc_type'], int(sug['doc_id']))} • confidenza {int(sug['confidence'] or 0)}")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ Conferma suggerimento", key=f"confirm_one_{sel_id}"):
                confirm_match(conn, int(sel_id), sug["doc_type"], int(sug["doc_id"]),
                              int(sug["confidence"]), float(sug["allocated_amount"] or abs(float(tx["amount"] or 0))), note="auto-confirm")
                st.success("Suggerimento confermato.")
                return
        with c2:
            if st.button("🧹 Scarta suggerimento", key=f"discard_one_{sel_id}"):
                conn.execute("DELETE FROM matches WHERE bank_tx_id=? AND status='SUGGESTED'", (int(sel_id),))
                conn.commit()
                st.warning("Suggerimento scartato (puoi comunque associare manualmente).")

    # candidati
    cands = candidate_docs_for_tx(conn, tx, topn=10)
    if not cands:
        st.warning("Nessun candidato trovato. Puoi segnare il movimento come IGNORATO o usare l'associazione manuale.")
        note = st.text_input("Nota (opzionale)", key=f"ignore_note_{sel_id}")
        if st.button("Segna come ignorato", key=f"ignore_{sel_id}"):
            ignore_tx(conn, int(sel_id), note=note)
            st.success("Movimento marcato come ignorato.")
        return

    st.markdown("### Candidati (automatici)")
    options = [f"{c['confidence']:3d} | {('🟢' if c['label']=='GREEN' else '🟡')} | {c['title']}" for c in cands]
    choice = st.radio("Seleziona il documento da associare", options, index=0, key=f"cand_{sel_id}")
    chosen = cands[options.index(choice)]

    alloc_default = float(abs(tx["amount"] or 0))
    allocated = st.number_input("Importo allocato (pagamenti parziali)", min_value=0.0, value=alloc_default, step=1.0, key=f"alloc_{sel_id}")
    note = st.text_input("Nota (opzionale)", key=f"note_{sel_id}")

    colb1, colb2 = st.columns(2)
    with colb1:
        if st.button("✅ Conferma associazione", key=f"conf_{sel_id}"):
            confirm_match(conn, int(sel_id), chosen["doc_type"], int(chosen["doc_id"]),
                          int(chosen["confidence"]), float(allocated), note=note)
            st.success("Associazione confermata e storicizzata.")
    with colb2:
        if st.button("🚫 Ignora movimento", key=f"ign_{sel_id}"):
            ignore_tx(conn, int(sel_id), note=note)
            st.success("Movimento marcato come ignorato.")

    # --- Associazione manuale (ricerca fatture) ---
    with st.expander("🔎 Associazione manuale (cerca fatture / controparte)", expanded=False):
        q = st.text_input("Cerca per nominativo / P.IVA / numero fattura", value="", key=f"manual_q_{sel_id}")
        if q.strip():
            qn = f"%{q.strip()}%"
            inv = pd.read_sql_query(
                """SELECT id, direction, party_name, party_vat, invoice_number, invoice_date, total_amount, currency
                   FROM invoices
                   WHERE party_name LIKE ? OR party_vat LIKE ? OR invoice_number LIKE ?
                   ORDER BY invoice_date DESC
                   LIMIT 200""",
                conn, params=(qn, qn, qn)
            )
            if inv.empty:
                st.info("Nessuna fattura trovata con questa ricerca.")
            else:
                inv["label"] = inv.apply(lambda r: f"[{r['direction']}] {r['invoice_date']} • Fatt. {r['invoice_number']} • {r['party_name']} • {float(r['total_amount'] or 0):.2f} {r['currency']}", axis=1)
                sel = st.selectbox("Seleziona fattura", inv["label"].tolist(), index=0, key=f"manual_sel_{sel_id}")
                inv_id = int(inv[inv["label"] == sel].iloc[0]["id"])
                if st.button("✅ Conferma associazione manuale", key=f"manual_conf_{sel_id}"):
                    confirm_match(conn, int(sel_id), "INVOICE", inv_id, 60, float(allocated), note="manual")
                    st.success("Associazione manuale confermata.")



def ui_parties(conn: sqlite3.Connection) -> None:
    st.subheader("Clienti / Fornitori")
    parties = parties_summary_df(conn)
    if parties.empty:
        st.info("Nessuna fattura caricata (oppure P.IVA non impostata per distinguere emesse/ricevute).")
        return

    st.dataframe(parties, use_container_width=True, hide_index=True)

    # drilldown
    names = parties["party_name"].fillna("").unique().tolist()
    sel = st.selectbox("Apri dettaglio parte", names, index=0)
    if not sel:
        return

    inv = pd.read_sql_query("""
        SELECT i.*,
               COALESCE(SUM(m.allocated_amount),0) AS paid
        FROM invoices i
        LEFT JOIN matches m
            ON m.doc_type='INVOICE' AND m.doc_id=i.id AND m.status='CONFIRMED'
        WHERE i.party_name=?
        GROUP BY i.id
        ORDER BY i.invoice_date DESC
    """, conn, params=(sel,))
    if inv.empty:
        st.info("Nessuna fattura per la parte selezionata.")
        return

    inv["outstanding"] = (inv["total_amount"].fillna(0.0) - inv["paid"].fillna(0.0)).clip(lower=0.0)
    st.markdown("### Dettaglio fatture")
    st.dataframe(inv[["direction","invoice_date","invoice_number","total_amount","paid","outstanding","currency"]], use_container_width=True, hide_index=True)


    with st.expander("✏️ Modifica fattura selezionata", expanded=False):
        inv2 = inv.copy()
        inv2["label"] = inv2.apply(lambda r: f"ID {int(r['id'])} • [{r['direction']}] {r['invoice_date']} • Fatt. {r['invoice_number']} • {r['party_name']} • {float(r['total_amount'] or 0):.2f} {r['currency']}", axis=1)
        choice = st.selectbox("Scegli fattura da modificare", inv2["label"].tolist(), index=0, key=f"edit_inv_{sel}")
        inv_id = int(inv2[inv2["label"] == choice].iloc[0]["id"])
        row = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
        if row:
            c1, c2 = st.columns(2)
            with c1:
                new_party = st.text_input("Controparte (nome)", value=str(row["party_name"] or ""), key=f"ed_inv_party_{inv_id}")
                new_vat = st.text_input("P.IVA (facoltativa)", value=str(row["party_vat"] or ""), key=f"ed_inv_vat_{inv_id}")
                new_num = st.text_input("Numero fattura", value=str(row["invoice_number"] or ""), key=f"ed_inv_num_{inv_id}")
            with c2:
                new_date = st.text_input("Data (YYYY-MM-DD)", value=str(row["invoice_date"] or ""), key=f"ed_inv_date_{inv_id}")
                new_due = st.text_input("Scadenza (YYYY-MM-DD)", value=str(row["due_date"] or ""), key=f"ed_inv_due_{inv_id}")
                new_total = st.text_input("Totale", value=str(row["total_amount"] if row["total_amount"] is not None else ""), key=f"ed_inv_tot_{inv_id}")
            new_ccy = st.text_input("Valuta", value=str(row["currency"] or "EUR"), key=f"ed_inv_ccy_{inv_id}")

            if st.button("Salva modifiche fattura", key=f"save_inv_{inv_id}"):
                inv_date = parse_date_any(new_date)
                due_date = parse_date_any(new_due)
                tot = parse_decimal(new_total)
                ccy = (new_ccy or "EUR").upper().strip()
                conn.execute(
                    """UPDATE invoices
                       SET party_name=?, party_name_norm=?, party_vat=?,
                           invoice_number=?, invoice_date=?, due_date=?,
                           total_amount=?, currency=?
                       WHERE id=?""",
                    (new_party, norm_text(new_party), new_vat, new_num, inv_date, due_date, tot, ccy, inv_id)
                )
                conn.commit()
                st.success("Fattura aggiornata.")


    # movimenti bancari collegati
    tx = pd.read_sql_query("""
        SELECT b.tx_date, b.amount, b.description, m.allocated_amount, m.created_at
        FROM matches m
        JOIN bank_tx b ON b.id=m.bank_tx_id
        JOIN invoices i ON i.id=m.doc_id
        WHERE m.doc_type='INVOICE' AND m.status='CONFIRMED' AND i.party_name=?
        ORDER BY b.tx_date DESC
    """, conn, params=(sel,))
    st.markdown("### Movimenti bancari collegati (confermati)")
    st.dataframe(tx, use_container_width=True, hide_index=True)


def ui_exports(conn: sqlite3.Connection) -> None:
    st.subheader("Export")
    c1, c2 = st.columns(2)

    with c1:
        if st.button("📄 Genera PDF report"):
            pdf = export_pdf_bytes(conn)
            st.download_button("⬇️ Scarica PDF", data=pdf, file_name="report_riconciliazione.pdf", mime="application/pdf")

    with c2:
        if st.button("📊 Genera Excel (XLSX)"):
            xls = export_excel_bytes(conn)
            st.download_button("⬇️ Scarica Excel", data=xls, file_name="riconciliazione.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def ui_db_stats(conn: sqlite3.Connection) -> None:
    st.subheader("Database / Storico")
    counts = {
        "Files": conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"],
        "Movimenti bancari": conn.execute("SELECT COUNT(*) c FROM bank_tx").fetchone()["c"],
        "Fatture": conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"],
        "SDD": conn.execute("SELECT COUNT(*) c FROM sdd").fetchone()["c"],
        "Esiti SDD": conn.execute("SELECT COUNT(*) c FROM sdd_outcomes").fetchone()["c"],
        "Match": conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"],
    }
    st.write(counts)

    with st.expander("Lista file caricati (ultimi 50)"):
        df = pd.read_sql_query("""
            SELECT uploaded_at, filename, kind, sha256 FROM files ORDER BY uploaded_at DESC LIMIT 50
        """, conn)
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.caption("Nota: su Streamlit Community Cloud lo storage può essere effimero. Per storicizzazione robusta usa un hosting con disco persistente o un DB gestito.")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)

    conn = get_conn()
    init_db(conn)
    sidebar_profile(conn)

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Caricamenti", "Riconciliazione", "Clienti/Fornitori", "Controlli", "Alias", "Export"
    ])

    with tab1:
        ui_uploads(conn)
        ui_db_stats(conn)

    with tab2:
        ui_reconciliation(conn)

    with tab3:
        ui_parties(conn)

    with tab4:
        ui_checks(conn)

    with tab5:
        ui_aliases(conn)

    with tab6:
        ui_exports(conn)


if __name__ == "__main__":
    main()
