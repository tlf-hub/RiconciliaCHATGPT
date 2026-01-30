import re
from typing import List, Dict, Optional, Tuple
from db import get_db, ensure_schema
from utils import sha256_text

def _ddmmyy_to_iso(s: str) -> Optional[str]:
    # e.g. 290126 -> 2026-01-29
    if not s or len(s) != 6:
        return None
    dd = int(s[0:2]); mm = int(s[2:4]); yy = int(s[4:6])
    yyyy = 2000 + yy if yy < 80 else 1900 + yy
    return f"{yyyy:04d}-{mm:02d}-{dd:02d}"

def _parse_amount(s: str) -> float:
    # 000000009805,22
    s = s.strip()
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0

def _move_hash(booking_date, value_date, amount, currency, description, reference):
    payload = f"{booking_date}|{value_date}|{amount}|{currency}|{description}|{reference}"
    return sha256_text(payload)

def parse_cbi_statement(blob: bytes, file_id: int):
    """
    Parsifica un estratto conto CBI testuale (record RH/61/62/63/64/EF) come quello allegato.
    Crea movimenti in bank_moves (source='cbi').
    - 61: saldo iniziale (data, segno, importo)
    - 62: movimento (date contabile/valuta, segno, importo, causale)
    - 63: descrizioni associate al movimento
    - 64: saldo finale
    """
    ensure_schema()
    text = blob.decode("latin-1", errors="replace")
    lines = text.splitlines()

    # Identify opening and closing balance (best effort)
    opening_balance = None
    closing_balance = None
    statement_date = None
    currency = "EUR"

    # Parse 61 (opening balance)
    for l in lines:
        if l.startswith(" 61"):
            # find EUR + date + sign + amount
            m = re.search(r'(EUR)(\d{6})([CD])(\d{12},\d{2})', l)
            if m:
                currency = m.group(1)
                statement_date = _ddmmyy_to_iso(m.group(2))
                sign = m.group(3)
                amt = _parse_amount(m.group(4))
                opening_balance = amt if sign == "C" else -amt
            break

    # Parse 64 (closing balance)
    for l in reversed(lines):
        if l.startswith(" 64"):
            m = re.search(r'(EUR)(\d{6})([CD])(\d{12},\d{2})', l)
            if m:
                currency = m.group(1)
                statement_date = statement_date or _ddmmyy_to_iso(m.group(2))
                sign = m.group(3)
                amt = _parse_amount(m.group(4))
                closing_balance = amt if sign == "C" else -amt
            break

    # Build movements from 62 + 63
    moves: List[Dict] = []
    curr = None

    for l in lines:
        if l.startswith(" 62"):
            # new movement
            # Example:  620000001001020126020126C000000000244,004844
            m = re.search(r'^\s62(\d{7})(\d{3})(\d{6})(\d{6})([CD])(\d{12},\d{2})(\d{4})', l)
            if not m:
                continue
            stmt_no = m.group(1)
            prog = m.group(2)
            booking = _ddmmyy_to_iso(m.group(3))
            value = _ddmmyy_to_iso(m.group(4))
            sign = m.group(5)
            amt = _parse_amount(m.group(6))
            caus = m.group(7)

            amount = amt if sign == "C" else -amt
            curr = {
                "stmt_no": stmt_no,
                "prog": prog,
                "booking_date": booking,
                "value_date": value,
                "amount": amount,
                "currency": currency,
                "causale": caus,
                "description_lines": []
            }
            moves.append(curr)

        elif l.startswith(" 63") and curr is not None:
            # continuation text for current movement; keep only printable chunk
            # Example:  630000001001ORDINE E CONTO
            txt = l[13:].rstrip()
            if txt:
                curr["description_lines"].append(txt)

    # Insert into DB computing running balance if opening is present
    con = get_db()
    running = opening_balance
    for m in moves:
        desc = " ".join([t.strip() for t in m["description_lines"] if t.strip()])
        reference = f"CBI:{m['stmt_no']}-{m['prog']}-CAUS:{m['causale']}"
        if running is not None:
            running = round((running + float(m["amount"])), 2)
            bal = running
        else:
            bal = None

        mh = _move_hash(m["booking_date"], m["value_date"], round(float(m["amount"]),2), m["currency"], desc, reference)

        con.execute("""INSERT OR IGNORE INTO bank_moves
            (file_id, source, booking_date, value_date, amount, currency, description, counterparty, reference, balance, move_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (file_id, "cbi",
             m["booking_date"], m["value_date"],
             float(m["amount"]), m["currency"],
             desc, "", reference, bal, mh
            ))

    con.commit()
    con.close()

    return {
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "statement_date": statement_date,
        "moves": len(moves),
        "currency": currency,
    }
