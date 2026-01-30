from xml.etree import ElementTree as ET
from db import get_db
from utils import parse_decimal

def parse_camt(root: ET.Element, file_id: int):
    con = get_db()
    for ntry in root.findall(".//{*}Ntry"):
        amt_el = ntry.find(".//{*}Amt")
        ind_el = ntry.find(".//{*}CdtDbtInd")
        bdt_el = ntry.find(".//{*}BookgDt//{*}Dt")
        vdt_el = ntry.find(".//{*}ValDt//{*}Dt")

        amt = float(parse_decimal(amt_el.text)) if amt_el is not None else 0.0
        ind = (ind_el.text or "").strip().upper() if ind_el is not None else "CRDT"
        amt = -abs(amt) if ind == "DBIT" else abs(amt)

        booking = bdt_el.text.strip() if bdt_el is not None and bdt_el.text else None
        value = vdt_el.text.strip() if vdt_el is not None and vdt_el.text else None

        ustrd = ntry.find(".//{*}Ustrd")
        desc = ustrd.text.strip() if ustrd is not None and ustrd.text else ""

        e2e = ntry.find(".//{*}Refs//{*}EndToEndId")
        ref = e2e.text.strip() if e2e is not None and e2e.text else ""

        nm = ntry.find(".//{*}RltdPties//{*}Dbtr//{*}Nm")
        if nm is None:
            nm = ntry.find(".//{*}RltdPties//{*}Cdtr//{*}Nm")
        cp = nm.text.strip() if nm is not None and nm.text else ""

        bal_el = root.find(".//{*}Bal//{*}Amt")
        bal = float(parse_decimal(bal_el.text)) if bal_el is not None else None

        con.execute("""INSERT OR IGNORE INTO bank_moves
            (file_id, source, booking_date, value_date, amount, currency, description, counterparty, reference, balance)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (file_id, "camt", booking, value, amt, "EUR", desc, cp, ref, bal))
    con.commit()
    con.close()
