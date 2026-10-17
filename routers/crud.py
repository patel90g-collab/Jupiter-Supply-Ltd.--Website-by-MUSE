"""
Jupiter Supply Ltd. — Master Dashboard CRUD API.

Every endpoint here uses the service_role Supabase client (server-side only),
so the dashboard can manage the full catalog, CRM, quotes, orders and invoices.
Customer-facing surfaces NEVER touch these routes.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from supabase import Client

from deps import get_supabase

router = APIRouter(prefix="/api", tags=["dashboard-crud"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _one_or_404(res: Any, entity: str) -> dict:
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail=f"{entity} not found")
    return rows[0]


def _list(supabase: Client, table: str, order: str = "created_at",
          limit: int = 200, filters: dict | None = None) -> list:
    q = supabase.table(table).select("*").order(order, desc=True).limit(limit)
    for k, v in (filters or {}).items():
        if v is not None:
            q = q.eq(k, v)
    return q.execute().data or []


# ---------------------------------------------------------------------------
# CATEGORIES
# ---------------------------------------------------------------------------
class CategoryIn(BaseModel):
    name: str
    slug: str | None = None
    parent_id: str | None = None
    sort_order: int = 0
    is_active: bool = True


@router.get("/categories")
def list_categories(supabase: Client = Depends(get_supabase)):
    return _list(supabase, "categories", order="sort_order")


@router.post("/categories", status_code=201)
def create_category(body: CategoryIn, supabase: Client = Depends(get_supabase)):
    data = body.model_dump()
    if not data.get("slug"):
        data["slug"] = data["name"].lower().replace(" ", "-").replace("&", "and")
    res = supabase.table("categories").insert(data).execute()
    return _one_or_404(res, "Category")


@router.put("/categories/{cid}")
def update_category(cid: str, body: CategoryIn, supabase: Client = Depends(get_supabase)):
    res = supabase.table("categories").update(body.model_dump(exclude_unset=True)).eq("id", cid).execute()
    return _one_or_404(res, "Category")


@router.delete("/categories/{cid}")
def delete_category(cid: str, supabase: Client = Depends(get_supabase)):
    # Soft-delete so products keep their history
    res = supabase.table("categories").update({"is_active": False}).eq("id", cid).execute()
    _one_or_404(res, "Category")
    return {"ok": True}


# ---------------------------------------------------------------------------
# PRODUCTS
# ---------------------------------------------------------------------------
class ProductIn(BaseModel):
    sku: str
    name: str
    description: str | None = None
    category_id: str | None = None
    supplier_id: str | None = None
    unit: str = "each"
    cost_price: float | None = None
    sell_price: float | None = None
    show_price: bool = False
    is_active: bool = True


@router.get("/products")
def list_products(supabase: Client = Depends(get_supabase),
                  category_id: str | None = None,
                  q: str | None = Query(default=None, max_length=120)):
    query = supabase.table("products").select(
        "*, categories(name), suppliers(name)").order("name").limit(500)
    if category_id:
        query = query.eq("category_id", category_id)
    if q:
        pattern = f"%{q.strip()}%"
        query = query.or_(f"name.ilike.{pattern},sku.ilike.{pattern}")
    return query.execute().data or []


@router.post("/products", status_code=201)
def create_product(body: ProductIn, supabase: Client = Depends(get_supabase)):
    res = supabase.table("products").insert(body.model_dump()).execute()
    return _one_or_404(res, "Product")


@router.put("/products/{pid}")
def update_product(pid: str, body: ProductIn, supabase: Client = Depends(get_supabase)):
    res = supabase.table("products").update(body.model_dump(exclude_unset=True)).eq("id", pid).execute()
    return _one_or_404(res, "Product")


@router.delete("/products/{pid}")
def delete_product(pid: str, supabase: Client = Depends(get_supabase)):
    res = supabase.table("products").update({"is_active": False}).eq("id", pid).execute()
    _one_or_404(res, "Product")
    return {"ok": True}


# ---------------------------------------------------------------------------
# SUPPLIERS (vendors)
# ---------------------------------------------------------------------------
class SupplierIn(BaseModel):
    name: str
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    country: str | None = None
    notes: str | None = None
    is_active: bool = True


@router.get("/suppliers")
def list_suppliers(supabase: Client = Depends(get_supabase)):
    return _list(supabase, "suppliers", order="name")


@router.post("/suppliers", status_code=201)
def create_supplier(body: SupplierIn, supabase: Client = Depends(get_supabase)):
    res = supabase.table("suppliers").insert(body.model_dump()).execute()
    return _one_or_404(res, "Supplier")


@router.put("/suppliers/{sid}")
def update_supplier(sid: str, body: SupplierIn, supabase: Client = Depends(get_supabase)):
    res = supabase.table("suppliers").update(body.model_dump(exclude_unset=True)).eq("id", sid).execute()
    return _one_or_404(res, "Supplier")


@router.delete("/suppliers/{sid}")
def delete_supplier(sid: str, supabase: Client = Depends(get_supabase)):
    res = supabase.table("suppliers").update({"is_active": False}).eq("id", sid).execute()
    _one_or_404(res, "Supplier")
    return {"ok": True}


# ---------------------------------------------------------------------------
# CUSTOMERS (clients)
# ---------------------------------------------------------------------------
class CustomerIn(BaseModel):
    company_name: str
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    customer_type: str = "hotel"
    notes: str | None = None


@router.get("/customers")
def list_customers(supabase: Client = Depends(get_supabase)):
    return _list(supabase, "customers", order="company_name")


@router.post("/customers", status_code=201)
def create_customer(body: CustomerIn, supabase: Client = Depends(get_supabase)):
    res = supabase.table("customers").insert(body.model_dump()).execute()
    return _one_or_404(res, "Customer")


@router.put("/customers/{cid}")
def update_customer(cid: str, body: CustomerIn, supabase: Client = Depends(get_supabase)):
    res = supabase.table("customers").update(body.model_dump(exclude_unset=True)).eq("id", cid).execute()
    return _one_or_404(res, "Customer")


@router.delete("/customers/{cid}")
def delete_customer(cid: str, supabase: Client = Depends(get_supabase)):
    res = supabase.table("customers").delete().eq("id", cid).execute()
    return {"ok": True}


# ---------------------------------------------------------------------------
# LEADS
# ---------------------------------------------------------------------------
class LeadIn(BaseModel):
    company_name: str
    contact_name: str | None = None
    phone: str | None = None
    email: str | None = None
    source: str | None = None
    status: str = "new"
    notes: str | None = None


@router.get("/leads")
def list_leads(supabase: Client = Depends(get_supabase),
               status_: str | None = Query(default=None, alias="status")):
    filters = {"status": status_} if status_ else None
    return _list(supabase, "leads", filters=filters)


@router.post("/leads", status_code=201)
def create_lead(body: LeadIn, supabase: Client = Depends(get_supabase)):
    res = supabase.table("leads").insert(body.model_dump()).execute()
    return _one_or_404(res, "Lead")


@router.put("/leads/{lid}")
def update_lead(lid: str, body: LeadIn, supabase: Client = Depends(get_supabase)):
    res = supabase.table("leads").update(body.model_dump(exclude_unset=True)).eq("id", lid).execute()
    return _one_or_404(res, "Lead")


@router.delete("/leads/{lid}")
def delete_lead(lid: str, supabase: Client = Depends(get_supabase)):
    supabase.table("leads").delete().eq("id", lid).execute()
    return {"ok": True}


@router.post("/leads/{lid}/convert", status_code=201)
def convert_lead(lid: str, supabase: Client = Depends(get_supabase)):
    """Turn a lead into a customer (and link its contacts)."""
    lead = _one_or_404(supabase.table("leads").select("*").eq("id", lid).execute(), "Lead")
    cust = _one_or_404(
        supabase.table("customers").insert({
            "company_name": lead["company_name"],
            "contact_name": lead.get("contact_name"),
            "email": lead.get("email"),
            "phone": lead.get("phone"),
            "notes": f"Converted from lead ({lead.get('source')})",
        }).execute(), "Customer")
    supabase.table("contacts").update({"customer_id": cust["id"], "lead_id": None}).eq("lead_id", lid).execute()
    supabase.table("leads").update({"status": "converted", "converted_customer_id": cust["id"]}).eq("id", lid).execute()
    return cust


# ---------------------------------------------------------------------------
# CONTACTS
# ---------------------------------------------------------------------------
class ContactIn(BaseModel):
    full_name: str
    role: str | None = None
    phone: str | None = None
    email: str | None = None
    customer_id: str | None = None
    lead_id: str | None = None
    notes: str | None = None


@router.get("/contacts")
def list_contacts(supabase: Client = Depends(get_supabase),
                  customer_id: str | None = None,
                  lead_id: str | None = None):
    filters = {}
    if customer_id:
        filters["customer_id"] = customer_id
    if lead_id:
        filters["lead_id"] = lead_id
    return _list(supabase, "contacts", order="full_name", filters=filters or None)


@router.post("/contacts", status_code=201)
def create_contact(body: ContactIn, supabase: Client = Depends(get_supabase)):
    if not body.customer_id and not body.lead_id:
        raise HTTPException(400, "Contact must link to a customer or a lead")
    res = supabase.table("contacts").insert(body.model_dump()).execute()
    return _one_or_404(res, "Contact")


@router.put("/contacts/{cid}")
def update_contact(cid: str, body: ContactIn, supabase: Client = Depends(get_supabase)):
    res = supabase.table("contacts").update(body.model_dump(exclude_unset=True)).eq("id", cid).execute()
    return _one_or_404(res, "Contact")


@router.delete("/contacts/{cid}")
def delete_contact(cid: str, supabase: Client = Depends(get_supabase)):
    supabase.table("contacts").delete().eq("id", cid).execute()
    return {"ok": True}


# ---------------------------------------------------------------------------
# QUOTES
# ---------------------------------------------------------------------------
class QuoteItemIn(BaseModel):
    product_id: str | None = None
    description: str = ""
    quantity: float = 1
    unit_price: float = 0


class QuoteIn(BaseModel):
    customer_id: str
    valid_until: date | None = None
    notes: str | None = None
    customer_note: str | None = None
    items: list[QuoteItemIn] = []


def _next_number(supabase: Client, table: str, column: str, prefix: str) -> str:
    res = supabase.table(table).select(column).order(column, desc=True).limit(1).execute()
    n = 0
    if res.data:
        try:
            n = int(res.data[0][column].split("-")[-1])
        except (ValueError, IndexError):
            n = 0
    return f"{prefix}-{n + 1:06d}"


@router.get("/quotes")
def list_quotes(supabase: Client = Depends(get_supabase)):
    rows = supabase.table("quotes").select("*, customers(company_name)").order("created_at", desc=True).limit(200).execute().data or []
    return rows


@router.get("/quotes/{qid}")
def get_quote(qid: str, supabase: Client = Depends(get_supabase)):
    q = _one_or_404(supabase.table("quotes").select("*, customers(*)").eq("id", qid).execute(), "Quote")
    q["items"] = supabase.table("quote_items").select("*, products(name, sku)").eq("quote_id", qid).execute().data or []
    return q


@router.post("/quotes", status_code=201)
def create_quote(body: QuoteIn, supabase: Client = Depends(get_supabase)):
    subtotal = round(sum(i.quantity * i.unit_price for i in body.items), 2)
    gst = round(subtotal * 0.05, 2)
    total = round(subtotal + gst, 2)
    q = _one_or_404(supabase.table("quotes").insert({
        "quote_number": _next_number(supabase, "quotes", "quote_number", "JS-Q"),
        "customer_id": body.customer_id,
        "status": "draft",
        "subtotal": subtotal, "gst": gst, "total": total,
        "valid_until": str(body.valid_until or (date.today() + timedelta(days=30))),
        "notes": body.notes, "customer_note": body.customer_note,
    }).execute(), "Quote")
    for it in body.items:
        supabase.table("quote_items").insert({
            "quote_id": q["id"], "product_id": it.product_id,
            "description": it.description or "",
            "quantity": it.quantity, "unit_price": it.unit_price,
            "line_total": round(it.quantity * it.unit_price, 2),
        }).execute()
    return get_quote(q["id"], supabase)


@router.put("/quotes/{qid}/status")
def set_quote_status(qid: str, status_: str = Query(alias="status"),
                     supabase: Client = Depends(get_supabase)):
    if status_ not in ("draft", "sent", "approved", "converted", "expired"):
        raise HTTPException(400, "Bad status")
    res = supabase.table("quotes").update({"status": status_}).eq("id", qid).execute()
    return _one_or_404(res, "Quote")


# ---------------------------------------------------------------------------
# INVOICES
# ---------------------------------------------------------------------------
class InvoiceItemIn(BaseModel):
    product_id: str | None = None
    description: str = ""
    quantity: float = 1
    unit_price: float = 0


class InvoiceIn(BaseModel):
    customer_id: str
    issue_date: date | None = None
    due_date: date | None = None
    customer_note: str | None = None
    items: list[InvoiceItemIn] = []


@router.get("/invoices")
def list_invoices(supabase: Client = Depends(get_supabase)):
    return supabase.table("invoices").select("*, customers(company_name)").order("created_at", desc=True).limit(200).execute().data or []


@router.get("/invoices/{iid}")
def get_invoice(iid: str, supabase: Client = Depends(get_supabase)):
    inv = _one_or_404(supabase.table("invoices").select("*, customers(*)").eq("id", iid).execute(), "Invoice")
    inv["items"] = supabase.table("invoice_items").select("*, products(name, sku)").eq("invoice_id", iid).execute().data or []
    inv["payments"] = supabase.table("payments").select("*").eq("invoice_id", iid).execute().data or []
    return inv


@router.post("/invoices", status_code=201)
def create_invoice(body: InvoiceIn, supabase: Client = Depends(get_supabase)):
    subtotal = round(sum(i.quantity * i.unit_price for i in body.items), 2)
    gst = round(subtotal * 0.05, 2)
    total = round(subtotal + gst, 2)
    inv = _one_or_404(supabase.table("invoices").insert({
        "invoice_number": _next_number(supabase, "invoices", "invoice_number", "JS"),
        "customer_id": body.customer_id,
        "status": "unpaid",
        "issue_date": str(body.issue_date or date.today()),
        "due_date": str(body.due_date or (date.today() + timedelta(days=30))),
        "subtotal": subtotal, "gst": gst, "total": total, "balance_due": total,
        "customer_note": body.customer_note,
    }).execute(), "Invoice")
    for it in body.items:
        supabase.table("invoice_items").insert({
            "invoice_id": inv["id"], "product_id": it.product_id,
            "description": it.description or "",
            "quantity": it.quantity, "unit_price": it.unit_price,
            "line_total": round(it.quantity * it.unit_price, 2),
        }).execute()
    return get_invoice(inv["id"], supabase)


class PaymentIn(BaseModel):
    amount: float
    payment_date: date | None = None
    method: str = "e-transfer"
    reference: str | None = None
    notes: str | None = None


@router.post("/invoices/{iid}/payments", status_code=201)
def record_payment(iid: str, body: PaymentIn, supabase: Client = Depends(get_supabase)):
    inv = _one_or_404(supabase.table("invoices").select("*").eq("id", iid).execute(), "Invoice")
    pay = _one_or_404(supabase.table("payments").insert({
        "invoice_id": iid,
        "amount": body.amount,
        "received_at": str(body.payment_date or date.today()),
        "method": body.method.replace("-", "_"),
        "reference": body.reference,
    }).execute(), "Payment")
    paid = supabase.table("payments").select("amount").eq("invoice_id", iid).execute().data or []
    total_paid = round(sum(float(p["amount"]) for p in paid), 2)
    balance = round(float(inv["total"]) - total_paid, 2)
    supabase.table("invoices").update({
        "balance_due": balance,
        "status": "paid" if balance <= 0 else "unpaid",
    }).eq("id", iid).execute()
    return pay


# ---------------------------------------------------------------------------
# DASHBOARD STATS
# ---------------------------------------------------------------------------
@router.get("/dashboard/stats")
def dashboard_stats(supabase: Client = Depends(get_supabase)):
    def count(table: str, filters: dict | None = None) -> int:
        q = supabase.table(table).select("id", count="exact")
        for k, v in (filters or {}).items():
            q = q.eq(k, v)
        return q.execute().count or 0

    invs = supabase.table("invoices").select("total,balance_due,status").execute().data or []
    receivable = round(sum(float(i.get("balance_due") or 0) for i in invs), 2)
    revenue = round(sum(float(i.get("total") or 0) for i in invs if i.get("status") == "paid"), 2)

    return {
        "leads_new": count("leads", {"status": "new"}),
        "leads_total": count("leads"),
        "customers": count("customers"),
        "suppliers": count("suppliers"),
        "products": count("products", {"is_active": True}),
        "categories": count("categories", {"is_active": True}),
        "quotes_draft": count("quotes", {"status": "draft"}),
        "invoices_unpaid": count("invoices", {"status": "unpaid"}),
        "receivable": receivable,
        "revenue_paid": revenue,
    }
