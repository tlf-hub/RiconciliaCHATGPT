from xml.etree import ElementTree as ET
from db import get_db, now_iso
from utils import xml_ns_strip, parse_decimal

def parse_pain008(root: ET.Element, file_id: int):
    con = get_db()
    for tx in root.iter():
        if xml_ns_strip(tx.tag) != "DrctDbtTxInf":
            continue
        instd = tx.find(".//{*}InstdAmt")
        amt = float(parse_decimal(instd.text)) if instd is not None else None
        due = tx.find(".//{*}ReqdColltnDt")
        due_date = due.text if due is not None else None
        e2e = tx.find(".//{*}EndToEndId")
        endtoend = e2e.text if e2e is not None else None
        dbtr = tx.find(".//{*}Dbtr//{*}Nm")
        debtor = dbtr.text if dbtr is not None else None
        mnd = tx.find(".//{*}MndtId")
        mandate = mnd.text if mnd is not None else None
        cdtr = tx.find(".//{*}Cdtr//{*}Nm")
        creditor = cdtr.text if cdtr is not None else None

        con.execute("""INSERT INTO sdd(file_id, debtor, amount, due_date, endtoend, mandate, creditor, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (file_id, debtor, amt, due_date, endtoend, mandate, creditor, now_iso()))
    con.commit()
    con.close()
