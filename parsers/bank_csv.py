import io
import pandas as pd
from db import get_db
from utils import parse_decimal, parse_date_any

TEMPLATE_COLUMNS = [
    "booking_date","value_date","amount","currency",
    "description","counterparty","reference","balance"
]

def parse_bank_csv(blob: bytes, file_id: int):
    df = pd.read_csv(io.BytesIO(blob))
    df.columns = [c.strip() for c in df.columns]
    con = get_db()
    for _, r in df.iterrows():
        booking = parse_date_any(r.get("booking_date"))
        value = parse_date_any(r.get("value_date"))
        amt = parse_decimal(r.get("amount"))
        bal = parse_decimal(r.get("balance"))
        con.execute("""INSERT OR IGNORE INTO bank_moves
            (file_id, source, booking_date, value_date, amount, currency, description, counterparty, reference, balance)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (file_id,"csv",
             str(booking) if booking else None,
             str(value) if value else None,
             float(amt) if amt is not None else 0.0,
             (r.get("currency") or "EUR"),
             (r.get("description") or ""),
             (r.get("counterparty") or ""),
             (r.get("reference") or ""),
             float(bal) if bal is not None else None))
    con.commit()
    con.close()
