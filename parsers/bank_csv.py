import io
import pandas as pd
from db import get_db, ensure_schema
from utils import parse_decimal, parse_date_any, sha256_text

TEMPLATE_COLUMNS = [
    "booking_date", "value_date", "amount", "currency",
    "description", "counterparty", "reference", "balance"
]

def _move_hash(booking_date, value_date, amount, currency, description, counterparty, reference):
    payload = f"{booking_date}|{value_date}|{amount}|{currency}|{description}|{counterparty}|{reference}"
    return sha256_text(payload)

def parse_bank_csv(blob: bytes, file_id: int):
    ensure_schema()
    df = pd.read_csv(io.BytesIO(blob))
    df.columns = [c.strip() for c in df.columns]
    con = get_db()
    for _, r in df.iterrows():
        booking = parse_date_any(r.get("booking_date"))
        value = parse_date_any(r.get("value_date"))
        amt = parse_decimal(r.get("amount"))
        bal = parse_decimal(r.get("balance"))
        currency = (r.get("currency") or "EUR")
        desc = (r.get("description") or "")
        cp = (r.get("counterparty") or "")
        ref = (r.get("reference") or "")
        mh = _move_hash(str(booking) if booking else "", str(value) if value else "", float(amt) if amt is not None else 0.0, currency, desc, cp, ref)

        con.execute("""INSERT OR IGNORE INTO bank_moves
            (file_id, source, booking_date, value_date, amount, currency, description, counterparty, reference, balance, move_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (file_id, "csv",
             str(booking) if booking else None,
             str(value) if value else None,
             float(amt) if amt is not None else 0.0,
             currency,
             desc, cp, ref,
             float(bal) if bal is not None else None,
             mh
            ))
    con.commit()
    con.close()
