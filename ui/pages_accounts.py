import streamlit as st
import pandas as pd
from db import get_db, log_audit
from accounting.chart import get_chart_df, upsert_account, set_party_map, ensure_default_chart

def render(state):
    st.subheader("Piano dei conti + mappa controparti")
    ensure_default_chart()
    user = state.get("user","admin")

    chart = get_chart_df()
    st.markdown("### Piano dei conti")
    st.dataframe(chart, use_container_width=True)

    with st.expander("➕ Aggiungi / modifica conto"):
        code = st.text_input("Codice", "")
        name = st.text_input("Descrizione", "")
        kind = st.selectbox("Tipo", ["asset","liability","equity","revenue","expense","suspense"])
        if st.button("Salva conto"):
            if code and name:
                upsert_account(code.strip(), name.strip(), kind)
                log_audit(user, "UPSERT_ACCOUNT", "chart_accounts", 0, None, {"code": code, "name": name, "kind": kind})
                st.success("Conto salvato.")
            else:
                st.warning("Compila codice e descrizione.")

    st.markdown("### Mappa controparte → conto (default)")
    con = get_db()
    parties = pd.read_sql("SELECT DISTINCT party, direction FROM invoices WHERE party IS NOT NULL AND party<>''", con)
    con.close()
    if parties.empty:
        st.info("Carica almeno una fattura.")
        return

    party = st.selectbox("Controparte", sorted(parties["party"].unique().tolist()))
    direction = st.selectbox("Direzione", ["emessa","ricevuta"])
    account_code = st.selectbox("Conto", chart["code"].tolist(),
                               format_func=lambda c: f"{c} - {chart[chart.code==c].iloc[0].name}")
    if st.button("Salva mappa"):
        set_party_map(party, direction, account_code)
        log_audit(user, "SET_PARTY_MAP", "party_account_map", 0, None, {"party": party, "direction": direction, "account_code": account_code})
        st.success("Mappa salvata.")
