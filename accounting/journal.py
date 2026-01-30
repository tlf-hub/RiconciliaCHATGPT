import pandas as pd
from db import get_db
from utils import parse_date_any

def _signed_total(total: float, is_credit_note: int) -> float:
    return -abs(total) if int(is_credit_note or 0) == 1 else abs(total)

def build_journal(start_date: str, end_date: str) -> pd.DataFrame:
    con = get_db()
    inv = pd.read_sql("SELECT * FROM invoices", con)
    bm = pd.read_sql("SELECT * FROM bank_moves", con)
    mt = pd.read_sql("SELECT * FROM matches WHERE confirmed=1", con)
    chart = pd.read_sql("SELECT * FROM chart_accounts", con)
    con.close()

    start = parse_date_any(start_date)
    end = parse_date_any(end_date)
    if start is None or end is None:
        return pd.DataFrame()

    def in_range(d):
        dd = parse_date_any(d)
        return dd is not None and start <= dd <= end

    chart_name = {str(r.code): str(r.name) for _, r in chart.iterrows()} if len(chart) else {}
    lines = []

    # Fatture (competenza)
    for _, r in inv.iterrows():
        if not in_range(r.invoice_date):
            continue
        signed = _signed_total(float(r.total or 0), int(r.is_credit_note or 0))
        direction = (r.direction or "").lower()
        acc_code = str(r.account_code or ("7000" if direction == "emessa" else "6100"))
        acc_name = chart_name.get(acc_code, str(r.account_name or ""))

        if direction == "emessa":
            if signed >= 0:
                lines += [
                    {"date": r.invoice_date, "doc":"FATT", "doc_id": int(r.id), "account_code":"1200", "account_name": chart_name.get("1200","Crediti v/clienti"), "dare": abs(signed), "avere": 0.0},
                    {"date": r.invoice_date, "doc":"FATT", "doc_id": int(r.id), "account_code": acc_code, "account_name": acc_name, "dare": 0.0, "avere": abs(signed)},
                ]
            else:
                lines += [
                    {"date": r.invoice_date, "doc":"NC", "doc_id": int(r.id), "account_code": acc_code, "account_name": acc_name, "dare": abs(signed), "avere": 0.0},
                    {"date": r.invoice_date, "doc":"NC", "doc_id": int(r.id), "account_code":"1200", "account_name": chart_name.get("1200","Crediti v/clienti"), "dare": 0.0, "avere": abs(signed)},
                ]
        else:
            if signed >= 0:
                lines += [
                    {"date": r.invoice_date, "doc":"FATT", "doc_id": int(r.id), "account_code": acc_code, "account_name": acc_name, "dare": abs(signed), "avere": 0.0},
                    {"date": r.invoice_date, "doc":"FATT", "doc_id": int(r.id), "account_code":"2200", "account_name": chart_name.get("2200","Debiti v/fornitori"), "dare": 0.0, "avere": abs(signed)},
                ]
            else:
                lines += [
                    {"date": r.invoice_date, "doc":"NC", "doc_id": int(r.id), "account_code":"2200", "account_name": chart_name.get("2200","Debiti v/fornitori"), "dare": abs(signed), "avere": 0.0},
                    {"date": r.invoice_date, "doc":"NC", "doc_id": int(r.id), "account_code": acc_code, "account_name": acc_name, "dare": 0.0, "avere": abs(signed)},
                ]

    # Banca (cassa) + sospesi
    if not bm.empty:
        bm = bm.copy()
        bm["alloc"] = 0.0
        if not mt.empty:
            for _, m in mt.iterrows():
                bm.loc[bm.id == m.bank_move_id, "alloc"] += float(m.allocated or 0)

        bm_p = bm[bm.booking_date.apply(in_range)].copy()

        # righe riconciliate
        if not mt.empty:
            inv_dir = inv[["id","direction"]].rename(columns={"id":"invoice_id"})
            mt2 = mt.merge(bm[["id","booking_date","amount"]], left_on="bank_move_id", right_on="id", how="left")
            mt2 = mt2.merge(inv_dir, on="invoice_id", how="left")
            for _, r in mt2.iterrows():
                if not in_range(r.booking_date):
                    continue
                alloc = float(r.allocated or 0)
                amt = float(r.amount or 0)
                direction = (r.direction or "").lower()

                # banca
                if amt >= 0:
                    lines.append({"date": r.booking_date, "doc":"PAG", "doc_id": int(r.bank_move_id), "account_code":"1010","account_name": chart_name.get("1010","Banca c/c"), "dare": alloc, "avere":0.0})
                else:
                    lines.append({"date": r.booking_date, "doc":"PAG", "doc_id": int(r.bank_move_id), "account_code":"1010","account_name": chart_name.get("1010","Banca c/c"), "dare": 0.0, "avere":alloc})

                # contropartita
                if direction == "emessa":
                    # incasso: Avere crediti
                    if amt >= 0:
                        lines.append({"date": r.booking_date, "doc":"PAG", "doc_id": int(r.bank_move_id), "account_code":"1200","account_name": chart_name.get("1200","Crediti v/clienti"), "dare":0.0, "avere":alloc})
                    else:
                        lines.append({"date": r.booking_date, "doc":"PAG", "doc_id": int(r.bank_move_id), "account_code":"1200","account_name": chart_name.get("1200","Crediti v/clienti"), "dare":alloc, "avere":0.0})
                else:
                    # pagamento: Dare debiti
                    if amt < 0:
                        lines.append({"date": r.booking_date, "doc":"PAG", "doc_id": int(r.bank_move_id), "account_code":"2200","account_name": chart_name.get("2200","Debiti v/fornitori"), "dare":alloc, "avere":0.0})
                    else:
                        lines.append({"date": r.booking_date, "doc":"PAG", "doc_id": int(r.bank_move_id), "account_code":"2200","account_name": chart_name.get("2200","Debiti v/fornitori"), "dare":0.0, "avere":alloc})

        # residui -> sospesi
        for _, r in bm_p.iterrows():
            amt = float(r.amount or 0)
            residual = round(max(0.0, abs(amt) - float(r.alloc or 0)), 2)
            if residual <= 0.01:
                continue
            if amt >= 0:
                lines += [
                    {"date": r.booking_date, "doc":"SOSP", "doc_id": int(r.id), "account_code":"1010","account_name": chart_name.get("1010","Banca c/c"), "dare": residual, "avere":0.0},
                    {"date": r.booking_date, "doc":"SOSP", "doc_id": int(r.id), "account_code":"1990","account_name": chart_name.get("1990","Sospesi"), "dare": 0.0, "avere":residual},
                ]
            else:
                lines += [
                    {"date": r.booking_date, "doc":"SOSP", "doc_id": int(r.id), "account_code":"1010","account_name": chart_name.get("1010","Banca c/c"), "dare": 0.0, "avere":residual},
                    {"date": r.booking_date, "doc":"SOSP", "doc_id": int(r.id), "account_code":"1990","account_name": chart_name.get("1990","Sospesi"), "dare": residual, "avere":0.0},
                ]

    j = pd.DataFrame(lines)
    if j.empty:
        return j
    j["dare"] = j["dare"].astype(float).round(2)
    j["avere"] = j["avere"].astype(float).round(2)
    return j.sort_values(["date","doc","doc_id","account_code"])

def trial_balance(journal: pd.DataFrame) -> pd.DataFrame:
    if journal is None or journal.empty:
        return pd.DataFrame()
    tb = journal.groupby(["account_code","account_name"], as_index=False)[["dare","avere"]].sum()
    tb["saldo"] = (tb["dare"] - tb["avere"]).round(2)
    tb["saldo_dare"] = tb["saldo"].apply(lambda x: x if x > 0 else 0.0)
    tb["saldo_avere"] = tb["saldo"].apply(lambda x: -x if x < 0 else 0.0)
    return tb.sort_values("account_code")
