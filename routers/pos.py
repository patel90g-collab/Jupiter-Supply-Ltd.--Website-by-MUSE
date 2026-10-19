"""
Jupiter Supply Ltd. — POS (Point of Sale) API.

Quick counter sale: pick products, take payment, print receipt.
Creates order + invoice + payment in one shot, deducts inventory.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Depends
from pydantic import BaseModel
from supabase import Client

from deps import get_supabase
from routers.auth import get_current_user
from routers.crud import _one_or_404, _next_number

router = APIRouter(prefix="/api/pos", tags=["pos"], dependencies=[Depends(get_current_user)])


class POSItem(BaseModel):
    product_id: str | None = None
    description: str = ""
    quantity: float = 1
    unit_price: float = 0


class POSSale(BaseModel):
    customer_id: str | None = None  # None = walk-in
    items: list[POSItem]
    payment_method: str = "cash"  # cash | card | e_transfer | cheque
    reference: str | None = None
    notes: str | None = None


@router.post("/sale", status_code=201)
def pos_sale(body: POSSale, supabase: Client = Depends(get_supabase)):
    # Walk-in customer: use or create a generic cash customer
    cid = body.customer_id
    if not cid:
        found = supabase.table("customers").select("id").eq("company_name", "Walk-in (POS)").limit(1).execute().data
        cid = found[0]["id"] if found else _one_or_404(
            supabase.table("customers").insert({
                "company_name": "Walk-in (POS)", "customer_type": "other",
                "notes": "Auto-created POS walk-in customer"}).execute(), "Customer")["id"]

    subtotal = round(sum(i.quantity * i.unit_price for i in body.items), 2)
    gst = round(subtotal * 0.05, 2)
    total = round(subtotal + gst, 2)

    order = _one_or_404(supabase.table("orders").insert({
        "order_number": _next_number(supabase, "orders", "order_number", "JS-O"),
        "customer_id": cid, "status": "fulfilled",
        "subtotal": subtotal, "gst": gst, "total": total,
        "notes": (body.notes or "") + " [POS sale]",
        "fulfilled_at": date.today().isoformat(),
    }).execute(), "Order")

    for it in body.items:
        supabase.table("order_items").insert({
            "order_id": order["id"], "product_id": it.product_id,
            "description": it.description, "quantity": it.quantity,
            "unit_price": it.unit_price,
            "line_total": round(it.quantity * it.unit_price, 2)}).execute()
        # Deduct inventory
        if it.product_id:
            supabase.table("stock_movements").insert({
                "product_id": it.product_id, "movement_type": "sale",
                "quantity": -abs(it.quantity),
                "reference": order["order_number"],
                "notes": "POS sale"}).execute()

    inv = _one_or_404(supabase.table("invoices").insert({
        "invoice_number": _next_number(supabase, "invoices", "invoice_number", "JS"),
        "customer_id": cid, "order_id": order["id"],
        "status": "paid", "issue_date": str(date.today()),
        "due_date": str(date.today()),
        "subtotal": subtotal, "gst": gst, "total": total, "balance_due": 0,
        "customer_note": "POS sale " + order["order_number"],
    }).execute(), "Invoice")

    for it in body.items:
        supabase.table("invoice_items").insert({
            "invoice_id": inv["id"], "product_id": it.product_id,
            "description": it.description, "quantity": it.quantity,
            "unit_price": it.unit_price,
            "line_total": round(it.quantity * it.unit_price, 2)}).execute()

    supabase.table("payments").insert({
        "invoice_id": inv["id"], "amount": total,
        "received_at": str(date.today()),
        "method": body.payment_method.replace("-", "_"),
        "reference": body.reference}).execute()

    return {"order_number": order["order_number"],
            "invoice_number": inv["invoice_number"],
            "total": total, "gst": gst, "subtotal": subtotal,
            "payment_method": body.payment_method}


@router.get("/today")
def pos_today(supabase: Client = Depends(get_supabase)):
    """Today's POS sales summary."""
    orders = supabase.table("orders").select("order_number,total,created_at").execute().data or []
    today = str(date.today())
    todays = [o for o in orders if str(o.get("created_at", ""))[:10] == today and (o.get("notes") or "").find("[POS sale]") >= 0]
    return {"date": today, "sales": len(todays),
            "total": round(sum(float(o.get("total") or 0) for o in todays), 2),
            "orders": todays}
