import streamlit as st
import pandas as pd
from db import get_db
from matching.engine import update_invoice_statuses

def render():
    st.subheader("Clienti/Fornitori: situazione")
    update_invoice_statuses()
    con = get_db()
    inv = pd.read_sql("SELECT * FROM invoices", con)
    mt = pd.read_sql("SELECT invoice_id, SUM(allocated) AS paid FROM matches WHERE confirmed=1 GROUP BY invoice_id", con)
    con.close()
    if inv.empty:
        st.info("Nessuna fattura.")
        return
    inv = inv.merge(mt, left_on="id", right_on="invoice_id", how="left")
    inv["paid"] = inv["paid"].fillna(0.0)
    inv["residual_calc"] = (inv["total"] - inv["paid"]).round(2)

    grp = inv.groupby(["direction","party"], as_index=False).agg(
        fatture=("id","count"),
        totale=("total","sum"),
        pagato=("paid","sum"),
        residuo=("residual_calc","sum"),
    ).sort_values(["direction","party"])

    st.dataframe(grp, use_container_width=True)

    party = st.selectbox("Controparte", sorted(inv["party"].dropna().unique().tolist()))
    direction = st.selectbox("Direzione", ["emessa","ricevuta"])
    det = inv[(inv.party==party) & (inv.direction==direction)].sort_values("invoice_date", ascending=False)
    st.dataframe(det[["id","invoice_date","number","total","paid","residual_calc","status","account_code","account_name"]], use_container_width=True)
