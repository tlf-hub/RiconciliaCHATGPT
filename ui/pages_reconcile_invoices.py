import streamlit as st
import pandas as pd
from db import get_db, log_audit
from matching.engine import suggest_matches_invoice, insert_match, update_invoice_statuses

def render(state):
    st.subheader("Riconciliazione fatture ↔ banca (split/acconti)")
    user = state.get("user","admin")

    con = get_db()
    bm = pd.read_sql("SELECT * FROM bank_moves ORDER BY booking_date DESC", con)
    inv = pd.read_sql("SELECT * FROM invoices ORDER BY invoice_date DESC", con)
    mt = pd.read_sql("""SELECT m.*, b.booking_date, b.amount AS bank_amount, i.direction, i.number, i.party, i.total, i.status
                        FROM matches m
                        JOIN bank_moves b ON b.id=m.bank_move_id
                        JOIN invoices i ON i.id=m.invoice_id
                        ORDER BY m.created_at DESC""", con)
    con.close()

    if st.button("🤖 Genera suggerimenti"):
        sug = suggest_matches_invoice()
        st.dataframe(sug, use_container_width=True) if not sug.empty else st.info("Nessun suggerimento.")

    if bm.empty or inv.empty:
        st.info("Carica movimenti e fatture.")
        return

    with st.form("match_form"):
        bank_move_id = st.selectbox("Movimento", bm["id"].tolist(),
            format_func=lambda x: f"#{x} | {bm[bm.id==x].iloc[0].booking_date} | {bm[bm.id==x].iloc[0].amount} | {str(bm[bm.id==x].iloc[0].description)[:60]}")
        invoice_id = st.selectbox("Fattura", inv["id"].tolist(),
            format_func=lambda x: f"#{x} | {inv[inv.id==x].iloc[0].direction} | {inv[inv.id==x].iloc[0].number} | {inv[inv.id==x].iloc[0].party} | {inv[inv.id==x].iloc[0].total} | {inv[inv.id==x].iloc[0].status}")
        allocated = st.number_input("Importo allocato", min_value=0.0, value=0.0, step=0.01)
        certainty = st.selectbox("Certezza", ["green","yellow"], index=0)
        confirmed = st.checkbox("Conferma", value=True)
        ok = st.form_submit_button("Salva match")

    if ok:
        insert_match(int(bank_move_id), int(invoice_id), float(allocated), certainty, 1 if confirmed else 0)
        log_audit(user, "UPSERT_MATCH", "matches", 0, None,
                  {"bank_move_id": int(bank_move_id), "invoice_id": int(invoice_id), "allocated": float(allocated), "certainty": certainty, "confirmed": confirmed})
        update_invoice_statuses()
        st.success("Match salvato; stato fattura aggiornato.")

    st.markdown("### Match")
    st.dataframe(mt, use_container_width=True)
