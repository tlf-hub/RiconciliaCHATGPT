import streamlit as st

from db import init_db, ensure_schema
from accounting.chart import ensure_default_chart
from ui.pages_upload import render as upload_page
from ui.pages_data import render as data_page
from ui.pages_reconcile_invoices import render as recon_inv_page
from ui.pages_reconcile_sdd import render as recon_sdd_page
from ui.pages_parties import render as parties_page
from ui.pages_accounts import render as accounts_page
from ui.pages_bilancino import render as bilancino_page
from ui.pages_audit import render as audit_page
from ui.pages_export import render as export_page

APP_VERSION = "1.4.1"

def main():
    st.set_page_config(page_title="Riconciliazione Contabile", layout="wide")
    st.title("Riconciliazione Contabile")
    st.caption(f"Versione {APP_VERSION} • Streamlit + SQLite")

    # Ensure DB schema exists even if a partial/empty DB file is present
    init_db()
    ensure_schema()
    ensure_default_chart()

    st.sidebar.header("Impostazioni")
    user = st.sidebar.text_input("Utente (Audit/Log)", value="admin")
    own_vat = st.sidebar.text_input("Tua P.IVA (per distinguere emesse/ricevute)", value="")
    st.sidebar.divider()

    menu = st.sidebar.radio("Menu", [
        "Upload",
        "Dati",
        "Riconciliazione Fatture",
        "Riconciliazione SDD",
        "Clienti/Fornitori",
        "Piano dei Conti",
        "Bilancino",
        "Audit/Log",
        "Export",
    ])

    state = {"user": user, "own_vat": own_vat}

    if menu == "Upload":
        upload_page(state)
    elif menu == "Dati":
        data_page()
    elif menu == "Riconciliazione Fatture":
        recon_inv_page(state)
    elif menu == "Riconciliazione SDD":
        recon_sdd_page(state)
    elif menu == "Clienti/Fornitori":
        parties_page()
    elif menu == "Piano dei Conti":
        accounts_page(state)
    elif menu == "Bilancino":
        bilancino_page()
    elif menu == "Audit/Log":
        audit_page()
    elif menu == "Export":
        export_page()

if __name__ == "__main__":
    main()
