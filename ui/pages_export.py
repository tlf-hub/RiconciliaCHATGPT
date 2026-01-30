import streamlit as st
import io
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from db import get_db

def _simple_pdf(title: str, lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 50
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, title)
    y -= 25
    c.setFont("Helvetica", 9)
    for ln in lines:
        if y < 60:
            c.showPage()
            y = h - 50
            c.setFont("Helvetica", 9)
        c.drawString(40, y, ln[:110])
        y -= 12
    c.showPage()
    c.save()
    return buf.getvalue()

def render():
    st.subheader("Export DB")
    con = get_db()
    bank = pd.read_sql("SELECT * FROM bank_moves", con)
    inv = pd.read_sql("SELECT * FROM invoices", con)
    sdd = pd.read_sql("SELECT * FROM sdd", con)
    matches = pd.read_sql("SELECT * FROM matches", con)
    sddm = pd.read_sql("SELECT * FROM sdd_matches", con)
    con.close()

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as w:
        bank.to_excel(w, "bank_moves", index=False)
        inv.to_excel(w, "invoices", index=False)
        sdd.to_excel(w, "sdd", index=False)
        matches.to_excel(w, "matches", index=False)
        sddm.to_excel(w, "sdd_matches", index=False)
    st.download_button("⬇️ Excel completo", out.getvalue(), file_name="export_completo.xlsx")

    if st.button("PDF riepilogo"):
        pdf = _simple_pdf("Riepilogo DB", [
            f"Movimenti: {len(bank)}",
            f"Fatture: {len(inv)}",
            f"SDD: {len(sdd)}",
            f"Match fatture: {len(matches)}",
            f"Match SDD: {len(sddm)}",
        ])
        st.download_button("⬇️ PDF riepilogo", pdf, file_name="riepilogo.pdf", mime="application/pdf")
