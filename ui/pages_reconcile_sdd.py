import streamlit as st
import pandas as pd
from db import get_db, ensure_schema, log_audit
from matching.engine import suggest_matches_sdd, insert_sdd_match

def render(state):
    st.subheader("Riconciliazione SDD ↔ incassi bancari")
    user = state.get("user","admin")
    ensure_schema()

    con = get_db()
    bm = pd.read_sql("SELECT * FROM bank_moves ORDER BY booking_date DESC", con)
    sdd = pd.read_sql("SELECT * FROM sdd ORDER BY created_at DESC", con)
    mt = pd.read_sql("""SELECT m.*, b.booking_date, b.amount AS bank_amount, s.debtor, s.amount AS sdd_amount, s.due_date, s.endtoend
                        FROM sdd_matches m
                        JOIN bank_moves b ON b.id=m.bank_move_id
                        JOIN sdd s ON s.id=m.sdd_id
                        ORDER BY m.created_at DESC""", con)
    con.close()

    st.markdown("#### Suggerimenti")
    if st.button("🤖 Genera suggerimenti SDD"):
        sug = suggest_matches_sdd()
        if sug.empty:
            st.info("Nessun suggerimento disponibile.")
        else:
            st.dataframe(sug, use_container_width=True)

    if bm.empty or sdd.empty:
        st.info("Carica movimenti e SDD.")
        return

    st.markdown("#### Inserisci/aggiorna match SDD")
    with st.form("sdd_form"):
        bank_move_id = st.selectbox(
            "Movimento bancario",
            bm["id"].tolist(),
            format_func=lambda x: f"#{x} | {bm[bm.id==x].iloc[0].booking_date} | {bm[bm.id==x].iloc[0].amount} | {str(bm[bm.id==x].iloc[0].description)[:60]}"
        )
        sdd_id = st.selectbox(
            "Riga SDD",
            sdd["id"].tolist(),
            format_func=lambda x: f"#{x} | {sdd[sdd.id==x].iloc[0].due_date} | {sdd[sdd.id==x].iloc[0].amount} | {sdd[sdd.id==x].iloc[0].debtor} | {sdd[sdd.id==x].iloc[0].endtoend}"
        )
        allocated = st.number_input("Importo allocato", min_value=0.0, value=0.0, step=0.01)
        certainty = st.selectbox("Certezza", ["green","yellow"], index=0)
        confirmed = st.checkbox("Conferma", value=True)
        ok = st.form_submit_button("Salva match SDD")

    if ok:
        insert_sdd_match(int(bank_move_id), int(sdd_id), float(allocated), certainty, 1 if confirmed else 0)
        log_audit(user, "UPSERT_SDD_MATCH", "sdd_matches", 0, None,
                  {"bank_move_id": int(bank_move_id), "sdd_id": int(sdd_id), "allocated": float(allocated), "certainty": certainty, "confirmed": confirmed})
        st.success("Match SDD salvato.")

    st.markdown("#### Match SDD esistenti")
    st.dataframe(mt, use_container_width=True)
