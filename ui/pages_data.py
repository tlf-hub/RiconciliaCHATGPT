import streamlit as st
import pandas as pd
from db import get_db, ensure_schema
from matching.engine import update_invoice_statuses

def render():
    st.subheader("Dati registrati")
    ensure_schema()
    con = get_db()
    bm = pd.read_sql("SELECT * FROM bank_moves ORDER BY booking_date DESC LIMIT 500", con)
    inv = pd.read_sql("SELECT * FROM invoices ORDER BY invoice_date DESC LIMIT 500", con)
    sdd = pd.read_sql("SELECT * FROM sdd ORDER BY created_at DESC LIMIT 500", con)
    con.close()

    c1, c2, c3 = st.columns(3)
    c1.metric("Movimenti bancari", len(bm))
    c2.metric("Fatture", len(inv))
    c3.metric("SDD", len(sdd))

    st.markdown("### Movimenti (ultimi 500)")
    st.dataframe(bm, use_container_width=True)

    st.markdown("### Fatture (ultime 500)")
    if st.button("Ricalcola stato fatture"):
        update_invoice_statuses()
        st.success("Stati ricalcolati.")
        con = get_db()
        inv = pd.read_sql("SELECT * FROM invoices ORDER BY invoice_date DESC LIMIT 500", con)
        con.close()
    st.dataframe(inv, use_container_width=True)

    st.markdown("### SDD (ultimi 500)")
    st.dataframe(sdd, use_container_width=True)
