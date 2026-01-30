import streamlit as st
import io
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from accounting.journal import build_journal, trial_balance

def _pdf_from_tb(tb: pd.DataFrame, title: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    y = height - 50
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, title)
    y -= 25
    c.setFont("Helvetica", 9)

    headers = ["Conto","Descrizione","Dare","Avere","Saldo Dare","Saldo Avere"]
    x = [40, 90, 330, 395, 460, 525]
    for i, h in enumerate(headers):
        c.drawString(x[i], y, h)
    y -= 10
    c.line(40, y, width-40, y)
    y -= 14

    for _, r in tb.iterrows():
        if y < 60:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 9)
        c.drawString(x[0], y, str(r["account_code"]))
        c.drawString(x[1], y, str(r["account_name"])[:35])
        c.drawRightString(x[2]+40, y, f'{float(r["dare"]):.2f}')
        c.drawRightString(x[3]+40, y, f'{float(r["avere"]):.2f}')
        c.drawRightString(x[4]+40, y, f'{float(r["saldo_dare"]):.2f}')
        c.drawRightString(x[5]+40, y, f'{float(r["saldo_avere"]):.2f}')
        y -= 12

    c.showPage()
    c.save()
    return buf.getvalue()

def render():
    st.subheader("Bilancino economico‑patrimoniale")
    c1, c2 = st.columns(2)
    start = c1.date_input("Data inizio", value=pd.Timestamp.today().date().replace(day=1))
    end = c2.date_input("Data fine", value=pd.Timestamp.today().date())

    if st.button("Genera bilancino"):
        journal = build_journal(str(start), str(end))
        if journal.empty:
            st.info("Nessuna scrittura nel periodo.")
            return
        tb = trial_balance(journal)
        st.dataframe(tb, use_container_width=True)

        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as w:
            journal.to_excel(w, "prima_nota", index=False)
            tb.to_excel(w, "bilancino", index=False)
        st.download_button("⬇️ Excel (prima nota + bilancino)", out.getvalue(), file_name="bilancino.xlsx")

        pdf = _pdf_from_tb(tb, f"Bilancino {start} - {end}")
        st.download_button("⬇️ PDF bilancino", pdf, file_name="bilancino.pdf", mime="application/pdf")
