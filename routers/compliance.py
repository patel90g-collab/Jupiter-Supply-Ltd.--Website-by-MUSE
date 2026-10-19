"""
Jupiter Supply Ltd. — Tax Compliance Desk API.

Filing deadlines, GST worksheet, and document checklist for RK Professional.
Read-only deadline data (from verified records); GST numbers live from accounting.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Depends
from supabase import Client

from deps import get_supabase
from routers.auth import get_current_user

router = APIRouter(prefix="/api/compliance", tags=["compliance"], dependencies=[Depends(get_current_user)])

# Verified from CRA filings & registry records (2026-10-17)
DEADLINES = [
    {"key": "gst2026", "title": "GST/HST Return 2026", "due": "2027-03-31",
     "detail": "Annual filer · BN 703217141 RT0001 · year-end Dec 31",
     "authority": "CRA", "status": "upcoming"},
    {"key": "t2_2026", "title": "T2 Corporate Return 2026", "due": "2027-06-30",
     "detail": "Alberta corp #2025305141 · prepared by RK Professional Corp",
     "authority": "CRA", "status": "upcoming"},
    {"key": "annual_return", "title": "Alberta Annual Return", "due": "2027-07-31",
     "detail": "Corp #2025305141 · due every July",
     "authority": "Alberta Registry", "status": "upcoming"},
    {"key": "biz_licence", "title": "Edmonton Business Licence Renewal", "due": "2027-01-26",
     "detail": "Licence #644299253-001 (Wholesale, Warehouse, Storage)",
     "authority": "City of Edmonton", "status": "upcoming"},
]

RK_CHECKLIST = [
    {"item": "Sales invoices for the year (from Jupiter system)", "done": False},
    {"item": "Vendor bills & receipts", "done": False},
    {"item": "Bank statements (RBC business)", "done": False},
    {"item": "E-transfer master file (auto-generated monthly)", "done": True},
    {"item": "Payroll records / contractor T4As (if any)", "done": False},
    {"item": "Vehicle logbook (if claiming vehicle expenses)", "done": False},
    {"item": "Prior year NOAs (2024, 2025 — requested from RK)", "done": False},
]


@router.get("/deadlines")
def deadlines():
    today = date.today()
    out = []
    for d in DEADLINES:
        due = date.fromisoformat(d["due"])
        days = (due - today).days
        out.append({**d, "days_left": days,
                    "urgency": "overdue" if days < 0 else ("soon" if days <= 60 else "ok")})
    return sorted(out, key=lambda x: x["due"])


@router.get("/gst-worksheet")
def gst_worksheet(supabase: Client = Depends(get_supabase), year: int = 2026):
    """GST filing worksheet: pulls live numbers, ready to hand to RK."""
    from routers.accounting import gst_tax_center
    tax = gst_tax_center(supabase, year)
    invs = supabase.table("invoices").select("invoice_number,total,gst,issue_date,status,customers(company_name)").execute().data or []
    bills = supabase.table("bills").select("bill_number,total,gst,issue_date,status,suppliers(name)").execute().data or []
    sales = [i for i in invs if i.get("status") != "void" and str(i.get("issue_date") or "").startswith(str(year))]
    purch = [b for b in bills if b.get("status") != "void" and str(b.get("issue_date") or "").startswith(str(year))]
    return {
        "year": year, **tax,
        "sales_lines": [{"number": i["invoice_number"], "customer": (i.get("customers") or {}).get("company_name"),
                         "total": float(i.get("total") or 0), "gst": float(i.get("gst") or 0)} for i in sales],
        "purchase_lines": [{"number": b["bill_number"], "supplier": (b.get("suppliers") or {}).get("name"),
                            "total": float(b.get("total") or 0), "gst": float(b.get("gst") or 0)} for b in purch],
        "prepared_for": "RK Professional Corp (Shabbir Banu)",
        "note": "Worksheet only — RK files the actual return. Not a substitute for your accountant.",
    }


@router.get("/rk-checklist")
def rk_checklist():
    return {"accountant": "RK Professional Corp · info@rk-cpa.ca · Shabbir Banu",
            "items": RK_CHECKLIST}
