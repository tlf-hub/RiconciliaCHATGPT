import streamlit as st
import pandas as pd
from db import get_db

def render():
    st.subheader("Audit / Log")
    con = get_db()
    df = pd.read_sql("SELECT * FROM audit_log ORDER BY created_at DESC", con)
    con.close()
    st.dataframe(df, use_container_width=True)
