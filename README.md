# Riconciliazione contabile automatica (Streamlit + SQLite)

App **standalone** (`app.py`) per riconciliare automaticamente:

- **Estratto conto bancario** (CSV)
- **Fatture elettroniche** (FatturaPA XML – emesse/ricevute)
- **SEPA SDD** (pain.008) + **esiti/insoluti** (pain.002 / camt.054 *best effort*)
- Match **verdi** (molto probabili) + match **gialli** (da confermare) + gestione **manuale**
- Storico completo: i file caricati vengono salvati nel DB (BLOB) e deduplicati via SHA256

> Nota importante: i dati contabili/bancari sono sensibili. Se deployi su Streamlit Cloud, valuta attentamente privacy e persistenza.

---

## Avvio locale

```bash
pip install -r requirements.txt
streamlit run app.py
```

Il DB SQLite viene creato nel file `recon.db` (o nel path indicato dalla variabile d’ambiente `RECON_DB_PATH`).

---

## Deploy su Streamlit Community Cloud

1. Crea un repo GitHub con:
   - `app.py`
   - `requirements.txt`
   - (consigliato) `.gitignore`
2. Su Streamlit Cloud: **New app** → scegli repo/branch → main file: `app.py`
3. (Opzionale) Imposta `RECON_DB_PATH` (ma su Streamlit Cloud lo storage può essere effimero).

Per storicizzazione davvero robusta: hosting con disco persistente o DB esterno.

---

## Input supportati

### 1) Estratto conto bancario (CSV)
Nell’app trovi un bottone per scaricare il **template CSV** con i campi consigliati:

- `date` (data contabile)
- `value_date` (data valuta)
- `description`
- `amount` (positivo = entrata, negativo = uscita)
- `currency`
- `balance` (facoltativo ma utile per controlli di coerenza)
- `counterparty_name`
- `counterparty_iban`
- `transaction_id` (facoltativo ma utile per dedup)

L’app prova anche a riconoscere intestazioni “simili” (es. `Data`, `Descrizione`, `Importo`, `Saldo`, ecc.) e **normalizza automaticamente**:
- date → `YYYY-MM-DD`
- importi → decimali (gestisce `1.234,56` / `1234.56` / simboli €)

Puoi caricare anche uno **ZIP** con più CSV.

### 2) Fatture elettroniche (XML FatturaPA)
Carica singoli XML o ZIP multipli. L’app:
- estrae data/numero/totale/divisa
- identifica (se imposti la tua P.IVA in sidebar) **EMESSA** vs **RICEVUTA**
- salva tutto nel DB e deduplica

### 3) SEPA SDD + esiti insoluti (XML)
- pain.008 → carico SDD (EndToEndId, importo, debitore, IBAN, mandato…)
- pain.002 / camt.054 → esiti/insoluti (best effort)

---

## Come funziona la riconciliazione

- Matching per **importo** + **somiglianza nominativo** + (dove possibile) **vicinanza temporale** + (se presente) numero fattura in descrizione
- L’app propone:
  - **🟢** candidati molto probabili
  - **🟡** candidati plausibili da confermare
- Sezione “Suggerimenti automatici”: crea match **🤖🟢** quando la confidenza supera una soglia (es. 90)

Puoi:
- confermare un match (storicizzato)
- scartare/ignorare un movimento
- cercare e associare manualmente una fattura

---

## Controlli di coerenza saldi

Se nel CSV è presente `balance`, l’app registra controlli:
- saldo progressivo *intra-file*
- confronto con il movimento precedente già registrato nel DB (*soft check*)

Le anomalie vengono mostrate nella tab “Controlli”.

---

## Clienti / Fornitori (sintesi + dettaglio)

Tab dedicata con:
- situazione per controparte: fatturato, incassato/pagato, residuo, saldi **Dare/Avere**
- drill-down su singola controparte con dettaglio fatture e movimenti collegati
- possibilità di **modificare i dati** di una fattura (nome/P.IVA/numero/date/importo)

---

## Export

- **Excel (XLSX)**: movimenti, fatture, SDD, esiti, match, controlli, riepilogo controparti
- **PDF**: riepilogo controparti + anomalie saldi

---

## Limitazioni (oneste)
- Non sostituisce un ERP: è una base robusta e “auditabile” per riconciliazione, con storico e correzioni.
- I tracciati SEPA esiti possono variare: la parte camt.054 è *best effort*.
- Su Streamlit Cloud lo storage può non essere persistente.

