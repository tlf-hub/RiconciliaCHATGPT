from xml.etree import ElementTree as ET
from db import get_db
from utils import parse_decimal

def parse_fatturapa(root: ET.Element, file_id: int, own_vat: str,
                    default_emessa=("7000","Ricavi vendite e prestazioni"),
                    default_ricevuta=("6100","Servizi"),
                    party_account_code_getter=None,
                    account_name_getter=None):
    header = root.find(".//{*}FatturaElettronicaHeader")
    body = root.find(".//{*}FatturaElettronicaBody")
    if header is None or body is None:
        return

    ced = header.find(".//{*}CedentePrestatore//{*}IdFiscaleIVA//{*}IdCodice")
    ced_vat = ced.text.strip() if ced is not None and ced.text else ""
    direction = "emessa" if own_vat and ced_vat == own_vat else "ricevuta"

    tipo = body.find(".//{*}DatiGeneraliDocumento//{*}TipoDocumento")
    tipo_doc = tipo.text.strip().upper() if tipo is not None and tipo.text else ""
    is_cn = 1 if tipo_doc == "TD04" else 0

    num = body.find(".//{*}DatiGeneraliDocumento//{*}Numero")
    dt = body.find(".//{*}DatiGeneraliDocumento//{*}Data")
    tot = body.find(".//{*}DatiGeneraliDocumento//{*}ImportoTotaleDocumento")

    number = num.text.strip() if num is not None and num.text else ""
    inv_date = dt.text.strip() if dt is not None and dt.text else None
    total = float(parse_decimal(tot.text)) if tot is not None else 0.0

    if direction == "ricevuta":
        party = header.find(".//{*}CedentePrestatore//{*}DatiAnagrafici//{*}Anagrafica//{*}Denominazione")
    else:
        party = header.find(".//{*}CessionarioCommittente//{*}DatiAnagrafici//{*}Anagrafica//{*}Denominazione")
    party_name = party.text.strip() if party is not None and party.text else ""

    mapped_code = party_account_code_getter(party_name, direction) if party_account_code_getter else None
    if mapped_code:
        acc_code = str(mapped_code)
        acc_name = account_name_getter(acc_code) if account_name_getter else ""
    else:
        acc_code, acc_name = default_emessa if direction == "emessa" else default_ricevuta

    con = get_db()
    con.execute("""INSERT OR IGNORE INTO invoices
        (file_id,direction,number,invoice_date,party,total,currency,is_credit_note,account_code,account_name,residual)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (file_id, direction, number, inv_date, party_name, abs(total), "EUR", is_cn, acc_code, acc_name, abs(total)))
    con.commit()
    con.close()
