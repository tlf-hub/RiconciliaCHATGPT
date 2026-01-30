# Riconciliazione Contabile (modulare) — v1.4.0

Supporta:
- Estratto conto CSV (template scaricabile)
- Estratto conto **CBI** (file `.cbi` / testo con record RH/61/62/63/64/EF)
- CAMT.053 / CAMT.054
- FatturaPA XML
- SDD SEPA pain.008 + SDD CBI (CBIBdySDDReq)
- Riconciliazione fatture ↔ banca (split/acconti) + stato fattura (aperta/parziale/saldata)
- Riconciliazione SDD ↔ incassi bancari
- Piano dei conti standard + mappa controparti → conto
- Bilancino per periodo (Excel + PDF)
- Audit/Log

## Avvio locale
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Nota su Streamlit Cloud
Il DB viene creato in `./data/recon.db` (cartella `data/`).  
Se il filesystem risultasse non scrivibile, l’app fa fallback su `/tmp/recon.db` (non persistente).
Puoi forzare il percorso con:
- `RECON_DB_PATH=/percorso/recon.db` oppure
- `RECON_DB_DIR=./data`
