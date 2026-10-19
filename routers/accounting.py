"""
Jupiter Supply Ltd. — QuickBooks-style Accounting API.

Chart of Accounts, Bills, Tax Center (GST/HST), aging reports,
customer statements, sales reports, P&L and Balance Sheet.
All server-side via service_role; never customer-facing.
"""
from __future__ import annotations

from datetime import date, timedelta, datetime

from fastapi import APIRouter, Depends, Depends, HTTPException, Query
from pydantic import BaseModel
from supabase import Client

from deps import get_supabase
from routers.auth import get_current_user
from routers.crud import _list, _one_or_404, _next_number

router = APIRouter(prefix="/api/accounting", tags=["accounting"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# CHART OF ACCOUNTS
# ---------------------------------------------------------------------------
@router.get("/chart")
def chart_of_accounts(supabase: Client = Depends(get_supabase)):
    return supabase.table("chart_of_accounts").select("*").order("code").execute().data or []


class AccountIn(BaseModel):
    code: str
    name: str
    type: str  # asset | liability | equity | income | expense
    subtype: str | None = None
    is_active: bool = True


@router.post("/chart", status_code=201)
def add_account(body: AccountIn, supabase: Client = Depends(get_supabase)):
    if body.type not in ("asset", "liability", "equity", "income", "expense"):
        raise HTTPException(400, "Bad account type")
    res = supabase.table("chart_of_accounts").insert(body.model_dump()).execute()
    return _one_or_404(res, "Account")


# ---------------------------------------------------------------------------
# BILLS (vendor bills / expenses)
# ---------------------------------------------------------------------------
class BillIn(BaseModel):
    supplier_id: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    subtotal: float = 0
    account_code: str | None = None
    notes: str | None = None


@router.get("/bills")
def list_bills(supabase: Client = Depends(get_supabase)):
    return supabase.table("bills").select("*, suppliers(name)").order("created_at", desc=True).limit(200).execute().data or []


@router.post("/bills", status_code=201)
def create_bill(body: BillIn, supabase: Client = Depends(get_supabase)):
    gst = round(body.subtotal * 0.05, 2)
    total = round(body.subtotal + gst, 2)
    res = supabase.table("bills").insert({
        "bill_number": _next_number(supabase, "bills", "bill_number", "BILL"),
        "supplier_id": body.supplier_id,
        "status": "unpaid",
        "issue_date": str(body.issue_date or date.today()),
        "due_date": str(body.due_date or (date.today() + timedelta(days=30))),
        "subtotal": round(body.subtotal, 2), "gst": gst, "total": total,
        "balance_due": total,
        "account_code": body.account_code, "notes": body.notes,
    }).execute()
    return _one_or_404(res, "Bill")


@router.put("/bills/{bid}/pay")
def pay_bill(bid: str, supabase: Client = Depends(get_supabase)):
    res = supabase.table("bills").update({"status": "paid", "balance_due": 0}).eq("id", bid).execute()
    return _one_or_404(res, "Bill")


@router.delete("/bills/{bid}")
def delete_bill(bid: str, supabase: Client = Depends(get_supabase)):
    supabase.table("bills").delete().eq("id", bid).execute()
    return {"ok": True}


# ---------------------------------------------------------------------------
# REPORTS — P&L and Balance Sheet (derived from invoices, bills, payments)
# ---------------------------------------------------------------------------
@router.get("/reports/profit-loss")
def profit_loss(supabase: Client = Depends(get_supabase),
                year: int | None = Query(default=None)):
    invs = supabase.table("invoices").select("total,issue_date,status").execute().data or []
    bills = supabase.table("bills").select("total,subtotal,account_code,issue_date,status").execute().data or []
    chart = {c["code"]: c for c in (supabase.table("chart_of_accounts").select("*").execute().data or [])}

    def in_year(d: str | None) -> bool:
        return not year or (d and str(d).startswith(str(year)))

    income = round(sum(float(i["total"]) for i in invs
                       if i["status"] in ("paid", "unpaid") and in_year(i.get("issue_date"))), 2)
    expenses_by_acct: dict[str, float] = {}
    for b in bills:
        if b["status"] == "void" or not in_year(b.get("issue_date")):
            continue
        code = b.get("account_code") or "5200"
        expenses_by_acct[code] = round(expenses_by_acct.get(code, 0) + float(b["total"]), 2)
    expenses = round(sum(expenses_by_acct.values()), 2)

    return {
        "year": year,
        "income_total": income,
        "income_lines": [{"account": "4000 - Sales - Hotel Supplies", "amount": income}],
        "expenses_total": expenses,
        "expense_lines": [
            {"account": f"{code} - {chart.get(code, {}).get('name', code)}", "amount": amt}
            for code, amt in sorted(expenses_by_acct.items())
        ],
        "net_profit": round(income - expenses, 2),
    }


@router.get("/reports/balance-sheet")
def balance_sheet(supabase: Client = Depends(get_supabase)):
    invs = supabase.table("invoices").select("balance_due,status").execute().data or []
    bills = supabase.table("bills").select("balance_due,status").execute().data or []

    ar = round(sum(float(i.get("balance_due") or 0) for i in invs if i.get("status") != "void"), 2)
    ap = round(sum(float(b.get("balance_due") or 0) for b in bills if b.get("status") != "void"), 2)
    # Cash is derived: paid invoices in minus paid bills out (simplified)
    paid_in = round(sum(float(i.get("total") or 0) - float(i.get("balance_due") or 0)
                        for i in invs if i.get("status") != "void"), 2)
    paid_out = round(sum(float(b.get("total") or 0) - float(b.get("balance_due") or 0)
                         for b in bills if b.get("status") != "void"), 2)
    cash = round(paid_in - paid_out, 2)

    return {
        "assets": [
            {"account": "1000 - Cash - RBC Business", "amount": cash},
            {"account": "1100 - Accounts Receivable", "amount": ar},
        ],
        "assets_total": round(cash + ar, 2),
        "liabilities": [
            {"account": "2000 - Accounts Payable", "amount": ap},
        ],
        "liabilities_total": ap,
        "equity_note": "Equity = Assets - Liabilities (simplified)",
    }


# ---------------------------------------------------------------------------
# TAX CENTER — GST/HST (Canada)
# ---------------------------------------------------------------------------
@router.get("/tax/gst")
def gst_tax_center(supabase: Client = Depends(get_supabase),
                   year: int | None = Query(default=None)):
    """GST collected on sales vs GST paid on purchases. Net = what you owe CRA (or get refunded)."""
    invs = supabase.table("invoices").select("gst,issue_date,status").execute().data or []
    bills = supabase.table("bills").select("gst,issue_date,status").execute().data or []

    def in_year(d):
        return not year or (d and str(d).startswith(str(year)))

    collected = round(sum(float(i.get("gst") or 0) for i in invs
                          if i.get("status") != "void" and in_year(i.get("issue_date"))), 2)
    paid = round(sum(float(b.get("gst") or 0) for b in bills
                     if b.get("status") != "void" and in_year(b.get("issue_date"))), 2)
    net = round(collected - paid, 2)
    return {
        "year": year or "all",
        "gst_collected": collected,
        "gst_paid_itc": paid,
        "net_owing": net,
        "position": "You OWE CRA" if net > 0 else ("CRA OWES YOU (refund)" if net < 0 else "Balanced"),
        "note": "Jupiter files GST annually. 2026 return due ~Mar 31, 2027.",
    }


# ---------------------------------------------------------------------------
# AGING REPORTS
# ---------------------------------------------------------------------------
def _aging_buckets(rows: list, date_key: str, bal_key: str, name_key: tuple) -> list:
    """Bucket unpaid balances by days overdue: Current, 1-30, 31-60, 61-90, 90+."""
    buckets = {"Current": [], "1-30": [], "31-60": [], "61-90": [], "90+": []}
    today = date.today()
    for r in rows:
        bal = float(r.get(bal_key) or 0)
        if bal <= 0:
            continue
        try:
            due = datetime.strptime(str(r.get(date_key))[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            due = today
        days = (today - due).days
        b = "Current" if days <= 0 else ("1-30" if days <= 30 else ("31-60" if days <= 60 else ("61-90" if days <= 90 else "90+")))
        nm = r.get(name_key[0]) or {}
        buckets[b].append({
            "id": r["id"],
            "number": r.get(name_key[1], ""),
            "name": nm.get(name_key[2], "—") if isinstance(nm, dict) else "—",
            "due": str(r.get(date_key))[:10],
            "days_overdue": max(days, 0),
            "balance": round(bal, 2),
        })
    out = []
    for b, items in buckets.items():
        items.sort(key=lambda x: -x["days_overdue"])
        out.append({"bucket": b, "count": len(items),
                    "total": round(sum(i["balance"] for i in items), 2), "items": items})
    return out


@router.get("/reports/aged-receivables")
def aged_receivables(supabase: Client = Depends(get_supabase)):
    invs = supabase.table("invoices").select("id,invoice_number,due_date,balance_due,status,customers(company_name)").execute().data or []
    open_invs = [i for i in invs if i.get("status") in ("unpaid", "overdue")]
    buckets = _aging_buckets(open_invs, "due_date", "balance_due", ("customers", "invoice_number", "company_name"))
    return {"buckets": buckets, "total": round(sum(b["total"] for b in buckets), 2)}


@router.get("/reports/aged-payables")
def aged_payables(supabase: Client = Depends(get_supabase)):
    bills = supabase.table("bills").select("id,bill_number,due_date,balance_due,status,suppliers(name)").execute().data or []
    open_bills = [b for b in bills if b.get("status") in ("unpaid", "overdue")]
    buckets = _aging_buckets(open_bills, "due_date", "balance_due", ("suppliers", "bill_number", "name"))
    return {"buckets": buckets, "total": round(sum(b["total"] for b in buckets), 2)}


# ---------------------------------------------------------------------------
# SALES REPORTS
# ---------------------------------------------------------------------------
@router.get("/reports/sales-by-customer")
def sales_by_customer(supabase: Client = Depends(get_supabase)):
    invs = supabase.table("invoices").select("total,status,customers(company_name)").execute().data or []
    by_cust: dict[str, float] = {}
    for i in invs:
        if i.get("status") == "void":
            continue
        nm = (i.get("customers") or {}).get("company_name", "Unknown")
        by_cust[nm] = round(by_cust.get(nm, 0) + float(i.get("total") or 0), 2)
    rows = sorted(by_cust.items(), key=lambda x: -x[1])
    return {"customers": [{"name": n, "total": t} for n, t in rows],
            "grand_total": round(sum(by_cust.values()), 2)}


@router.get("/reports/cash-flow")
def cash_flow(supabase: Client = Depends(get_supabase)):
    """Money in (payments received) vs money out (bills paid)."""
    pays = supabase.table("payments").select("amount,received_at").execute().data or []
    in_total = round(sum(float(p.get("amount") or 0) for p in pays), 2)
    bills = supabase.table("bills").select("total,balance_due,status").execute().data or []
    out_total = round(sum(float(b.get("total") or 0) - float(b.get("balance_due") or 0)
                          for b in bills if b.get("status") != "void"), 2)
    return {"in": in_total, "out": out_total, "net": round(in_total - out_total, 2),
            "note": "Cash in = payments recorded on invoices. Cash out = bills marked paid."}


# ---------------------------------------------------------------------------
# CUSTOMER STATEMENT
# ---------------------------------------------------------------------------
@router.get("/customers/{cid}/statement")
def customer_statement(cid: str, supabase: Client = Depends(get_supabase)):
    cust = _one_or_404(supabase.table("customers").select("*").eq("id", cid).execute(), "Customer")
    invs = supabase.table("invoices").select("*").eq("customer_id", cid).order("issue_date", desc=True).execute().data or []
    lines = []
    for inv in invs:
        pays = supabase.table("payments").select("*").eq("invoice_id", inv["id"]).execute().data or []
        lines.append({
            "number": inv["invoice_number"], "date": str(inv.get("issue_date"))[:10] if inv.get("issue_date") else "—",
            "total": float(inv.get("total") or 0), "balance": float(inv.get("balance_due") or 0),
            "status": inv.get("status"),
            "payments": [{"date": str(p.get("received_at"))[:10], "amount": float(p.get("amount") or 0), "method": p.get("method")} for p in pays],
        })
    total_billed = round(sum(l["total"] for l in lines), 2)
    total_due = round(sum(l["balance"] for l in lines), 2)
    return {"customer": cust, "invoices": lines,
            "total_billed": total_billed, "total_paid": round(total_billed - total_due, 2), "balance_due": total_due}
