import streamlit as st
import pandas as pd
from db import get_db, ensure_schema

def render():
    st.subheader("Audit / Log")
    ensure_schema()
    con = get_db()
    df = pd.read_sql("SELECT * FROM audit_log ORDER BY created_at DESC", con)
    con.close()
    st.dataframe(df, use_container_width=True)
