import pandas as pd
from db import get_db, now_iso
from utils import normalize_text, parse_date_any

def _score_invoice_bank(inv_row, bm_row) -> float:
    score = 0.0
    inv_amt = float(inv_row.total or 0)
    bm_amt = abs(float(bm_row.amount or 0))
    if abs(inv_amt - bm_amt) < 0.01:
        score += 70

    party = normalize_text(inv_row.party or "")
    hay = normalize_text((bm_row.description or "") + " " + (bm_row.counterparty or "") + " " + (bm_row.reference or ""))
    if party and party[:8] in hay:
        score += 15
    if inv_row.number and normalize_text(inv_row.number) in hay:
        score += 10

    idt = parse_date_any(inv_row.invoice_date)
    bdt = parse_date_any(bm_row.booking_date)
    if idt and bdt and abs((bdt - idt).days) <= 10:
        score += 5
    return score

def suggest_matches_invoice(limit_per_bank: int = 5) -> pd.DataFrame:
    con = get_db()
    inv = pd.read_sql("SELECT * FROM invoices", con)
    bm = pd.read_sql("SELECT * FROM bank_moves", con)
    existing = pd.read_sql("SELECT bank_move_id, invoice_id FROM matches", con)
    con.close()

    existing_set = set((int(r.bank_move_id), int(r.invoice_id)) for _, r in existing.iterrows()) if len(existing) else set()

    rows = []
    for _, b in bm.iterrows():
        cands = []
        for _, i in inv.iterrows():
            if (int(b.id), int(i.id)) in existing_set:
                continue
            sc = _score_invoice_bank(i, b)
            if sc <= 0:
                continue
            cert = "green" if sc >= 85 else ("yellow" if sc >= 60 else "red")
            cands.append((sc, cert, int(b.id), int(i.id), float(abs(float(b.amount or 0))), float(i.total or 0)))
        cands.sort(reverse=True, key=lambda x: x[0])
        for sc, cert, bm_id, inv_id, bm_abs, inv_amt in cands[:limit_per_bank]:
            rows.append({"bank_move_id": bm_id, "invoice_id": inv_id, "score": round(sc,2), "certainty": cert, "suggested_alloc": round(min(bm_abs, inv_amt),2)})
    return pd.DataFrame(rows)

def insert_match(bank_move_id: int, invoice_id: int, allocated: float, certainty: str, confirmed: int):
    con = get_db()
    con.execute("""INSERT INTO matches(bank_move_id,invoice_id,allocated,certainty,confirmed,created_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(bank_move_id,invoice_id) DO UPDATE SET
                     allocated=excluded.allocated,
                     certainty=excluded.certainty,
                     confirmed=excluded.confirmed,
                     created_at=excluded.created_at""",
                (bank_move_id, invoice_id, float(allocated), str(certainty), int(confirmed), now_iso()))
    con.commit()
    con.close()

def update_invoice_statuses():
    con = get_db()
    inv = pd.read_sql("SELECT id,total FROM invoices", con)
    mt = pd.read_sql("SELECT invoice_id, SUM(allocated) AS paid FROM matches WHERE confirmed=1 GROUP BY invoice_id", con)
    con.close()
    paid_map = {int(r.invoice_id): float(r.paid or 0.0) for _, r in mt.iterrows()} if len(mt) else {}

    con = get_db()
    for _, r in inv.iterrows():
        inv_id = int(r.id)
        total = float(r.total or 0)
        paid = float(paid_map.get(inv_id, 0.0))
        residual = round(max(0.0, total - paid), 2)
        if paid <= 0.01:
            status = "aperta"
        elif residual <= 0.01:
            status = "saldata"
        else:
            status = "parziale"
        con.execute("UPDATE invoices SET paid_amount=?, residual=?, status=? WHERE id=?",
                    (round(paid,2), residual, status, inv_id))
    con.commit()
    con.close()

# SDD -> BANK

def _score_sdd_bank(sdd_row, bm_row) -> float:
    score = 0.0
    s_amt = float(sdd_row.amount or 0)
    b_amt = abs(float(bm_row.amount or 0))
    if abs(s_amt - b_amt) < 0.01:
        score += 70

    hay = normalize_text((bm_row.description or "") + " " + (bm_row.counterparty or "") + " " + (bm_row.reference or ""))
    e2e = normalize_text(sdd_row.endtoend or "")
    if e2e and e2e in hay:
        score += 20
    debtor = normalize_text(sdd_row.debtor or "")
    if debtor and debtor[:8] in hay:
        score += 10

    due = parse_date_any(sdd_row.due_date)
    bdt = parse_date_any(bm_row.booking_date)
    if due and bdt and abs((bdt - due).days) <= 5:
        score += 5
    return score

def suggest_matches_sdd(limit_per_bank: int = 5) -> pd.DataFrame:
    con = get_db()
    sdd = pd.read_sql("SELECT * FROM sdd", con)
    bm = pd.read_sql("SELECT * FROM bank_moves", con)
    existing = pd.read_sql("SELECT bank_move_id, sdd_id FROM sdd_matches", con)
    con.close()
    existing_set = set((int(r.bank_move_id), int(r.sdd_id)) for _, r in existing.iterrows()) if len(existing) else set()

    rows = []
    for _, b in bm.iterrows():
        cands = []
        for _, s in sdd.iterrows():
            if (int(b.id), int(s.id)) in existing_set:
                continue
            sc = _score_sdd_bank(s, b)
            if sc <= 0:
                continue
            cert = "green" if sc >= 90 else ("yellow" if sc >= 65 else "red")
            cands.append((sc, cert, int(b.id), int(s.id), float(abs(float(b.amount or 0))), float(s.amount or 0)))
        cands.sort(reverse=True, key=lambda x: x[0])
        for sc, cert, bm_id, sdd_id, bm_abs, s_amt in cands[:limit_per_bank]:
            rows.append({"bank_move_id": bm_id, "sdd_id": sdd_id, "score": round(sc,2), "certainty": cert, "suggested_alloc": round(min(bm_abs, s_amt),2)})
    return pd.DataFrame(rows)

def insert_sdd_match(bank_move_id: int, sdd_id: int, allocated: float, certainty: str, confirmed: int):
    con = get_db()
    con.execute("""INSERT INTO sdd_matches(bank_move_id,sdd_id,allocated,certainty,confirmed,created_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(bank_move_id,sdd_id) DO UPDATE SET
                     allocated=excluded.allocated,
                     certainty=excluded.certainty,
                     confirmed=excluded.confirmed,
                     created_at=excluded.created_at""",
                (bank_move_id, sdd_id, float(allocated), str(certainty), int(confirmed), now_iso()))
    con.commit()
    con.close()
