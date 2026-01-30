import streamlit as st
import io, zipfile
from xml.etree import ElementTree as ET

from db import save_file
from utils import sha256_bytes
from parsers.detect import detect_xml_kind
from parsers.bank_csv import parse_bank_csv, TEMPLATE_COLUMNS
from parsers.cbi_sdd import parse_cbi_sdd
from parsers.pain008 import parse_pain008
from parsers.camt import parse_camt
from parsers.fatturapa import parse_fatturapa
from parsers.cbi_ec import parse_cbi_statement
from accounting.chart import get_party_account_code, get_account_name

def _template_csv_bytes():
    import pandas as pd
    df = pd.DataFrame(columns=TEMPLATE_COLUMNS)
    out = io.StringIO()
    df.to_csv(out, index=False)
    return out.getvalue().encode("utf-8")

def _parse_one_xml(blob: bytes, file_id: int, own_vat: str):
    root = ET.fromstring(blob)
    kind = detect_xml_kind(root)
    if kind == "cbi_sdd":
        parse_cbi_sdd(root, file_id)
        return "SDD_CBI"
    if kind == "pain008":
        parse_pain008(root, file_id)
        return "SDD_pain008"
    if kind in ("camt053","camt054"):
        parse_camt(root, file_id)
        return kind
    if kind == "fatturapa":
        parse_fatturapa(root, file_id, own_vat,
                        party_account_code_getter=get_party_account_code,
                        account_name_getter=get_account_name)
        return "FatturaPA"
    return "UNKNOWN"

def render(state):
    st.subheader("Upload documenti")
    st.caption("Supporta: CSV estratto conto, CBI (file .cbi), CAMT.053/054, FatturaPA, SDD pain.008 e SDD CBI. Anche ZIP.")

    st.download_button("⬇️ Scarica template CSV estratto conto", data=_template_csv_bytes(),
                       file_name="template_estratto_conto.csv", mime="text/csv")

    files = st.file_uploader("Carica file (csv/xml/cbi/zip)", type=["csv","xml","zip","cbi","txt"], accept_multiple_files=True)
    if not files:
        return

    own_vat = (state.get("own_vat") or "").strip()
    uploaded = skipped = parsed = 0
    notes = []

    for f in files:
        raw = f.read()
        sha = sha256_bytes(raw)
        try:
            file_id, is_new = save_file(f.name, f.type or "", sha, raw)
        except Exception as e:
            st.error(f"Errore DB in save_file su {f.name}: {e}")
            st.stop()

        if not is_new:
            skipped += 1
            continue
        uploaded += 1

        try:
            name = f.name.lower()
            if name.endswith(".csv"):
                parse_bank_csv(raw, file_id)
                parsed += 1
                notes.append(f"✅ {f.name}: CSV parsato")
            elif name.endswith(".cbi") or name.endswith(".txt"):
                info = parse_cbi_statement(raw, file_id)
                parsed += 1
                notes.append(f"✅ {f.name}: CBI parsato (movimenti: {info.get('moves')}, saldo iniziale: {info.get('opening_balance')}, saldo finale: {info.get('closing_balance')})")
            elif name.endswith(".xml"):
                kind = _parse_one_xml(raw, file_id, own_vat)
                if kind == "UNKNOWN":
                    notes.append(f"⚠️ {f.name}: XML archiviato ma non riconosciuto")
                else:
                    parsed += 1
                    notes.append(f"✅ {f.name}: {kind}")
            elif name.endswith(".zip"):
                z = zipfile.ZipFile(io.BytesIO(raw))
                for n in z.namelist():
                    bb = z.read(n)
                    if n.lower().endswith(".csv"):
                        parse_bank_csv(bb, file_id); parsed += 1
                        notes.append(f"✅ {n}: CSV (da zip)")
                    elif n.lower().endswith(".cbi") or n.lower().endswith(".txt"):
                        info = parse_cbi_statement(bb, file_id); parsed += 1
                        notes.append(f"✅ {n}: CBI (da zip) movimenti: {info.get('moves')}")
                    elif n.lower().endswith(".xml"):
                        kind = _parse_one_xml(bb, file_id, own_vat)
                        if kind == "UNKNOWN":
                            notes.append(f"⚠️ {n}: XML (da zip) archiviato ma non riconosciuto")
                        else:
                            parsed += 1
                            notes.append(f"✅ {n}: {kind} (da zip)")
            else:
                notes.append(f"⚠️ {f.name}: estensione non supportata")
        except Exception as e:
            notes.append(f"❌ {f.name}: errore parsing: {e}")

    st.success(f"Nuovi: {uploaded} | duplicati ignorati: {skipped} | parsati: {parsed}")
    for n in notes:
        st.write(n)
