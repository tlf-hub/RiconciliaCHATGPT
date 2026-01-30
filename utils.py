import hashlib
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Optional

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def xml_ns_strip(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag

def parse_decimal(x) -> Optional[Decimal]:
    if x is None:
        return None
    if isinstance(x, (int, float, Decimal)):
        return Decimal(str(x))
    s = str(x).strip().replace(" ", "")
    if s.count(",") == 1 and s.count(".") >= 1:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None

def parse_date_any(s) -> Optional[date]:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z","")).date()
    except Exception:
        return None

def normalize_text(s: str) -> str:
    if s is None:
        return ""
    return " ".join(str(s).strip().lower().split())
