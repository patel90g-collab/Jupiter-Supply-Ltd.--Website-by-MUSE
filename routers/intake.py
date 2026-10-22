"""
Jupiter Supply Ltd. — AI Intake API.

Upload a vendor invoice (PDF/photo) and AI extracts the vendor, line items
and totals, then one confirmation creates:
  - the supplier (find-or-create by name),
  - one product per line item (purchase price = cost, sell = cost + 38% markup),
  - the vendor bill (AP).

Also: add a product from a website URL or a manually uploaded photo.
All endpoints are JWT-protected (master admin). Analyze steps never write.
"""
from __future__ import annotations

import base64
import io
import json
import random
import re
import string
from datetime import date

import fitz  # pymupdf
import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from supabase import Client

from deps import get_http, get_settings, get_supabase, nvidia_headers
from routers.auth import get_current_user

router = APIRouter(prefix="/api/intake", tags=["intake"],
                   dependencies=[Depends(get_current_user)])

MARKUP = 1.38  # 38% markup on purchase cost
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_PDF_BYTES = 15 * 1024 * 1024
ALLOWED_UNITS = ("each", "dozen", "case", "pack", "box")

INVOICE_SCHEMA_HINT = """{
  "vendor": {"name": "", "email": "", "phone": "", "address": ""},
  "invoice_number": "", "invoice_date": "", "due_date": "",
  "currency": "CAD",
  "line_items": [
    {"sku": "", "description": "", "quantity": 1, "unit": "each", "unit_price": 0.0}
  ],
  "subtotal": 0.0, "tax": 0.0, "shipping": 0.0, "total": 0.0
}"""


# ---------------------------------------------------------------------------
# NVIDIA helpers
# ---------------------------------------------------------------------------
async def _nvidia_chat(messages: list[dict], max_tokens: int = 2048) -> str:
    settings = get_settings()
    if not settings.nvidia_api_key:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "AI engine not configured")
    payload = {"model": settings.nvidia_vision_model, "temperature": 0.1,
               "max_tokens": max_tokens, "messages": messages}
    http = await get_http()
    try:
        resp = await http.post(f"{settings.nvidia_base_url}/chat/completions",
                               headers=nvidia_headers(settings), json=payload,
                               timeout=120.0)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"AI model error: {exc.response.status_code}")
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"AI model unreachable: {exc}")


def _clean_json(content: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(),
                     flags=re.MULTILINE).strip()
    if not cleaned.startswith("{"):
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        cleaned = m.group(0) if m else cleaned
    try:
        return json.loads(cleaned)
    except Exception:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "AI did not return usable data — try a clearer photo/scan")


# ---------------------------------------------------------------------------
# Small DB helpers
# ---------------------------------------------------------------------------
def _map_unit(raw: str | None) -> str:
    r = (raw or "").strip().lower()
    mapping = {"each": "each", "ea": "each", "pc": "each", "pcs": "each",
               "piece": "each", "pieces": "each", "unit": "each", "units": "each",
               "dozen": "dozen", "dz": "dozen", "case": "case", "cs": "case",
               "ctn": "case", "pack": "pack", "pk": "pack", "box": "box", "bx": "box"}
    return mapping.get(r, "each")


def _parse_date(raw) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d",
                "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%m-%d-%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    return m.group(0) if m else None


def _find_or_create_supplier(supabase: Client, vendor: dict) -> dict:
    name = (vendor.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Vendor name is required")
    existing = supabase.table("suppliers").select("*") \
        .ilike("name", name).limit(1).execute().data
    if existing:
        return existing[0]
    res = supabase.table("suppliers").insert({
        "name": name,
        "contact_name": (vendor.get("contact_name") or "").strip() or None,
        "email": (vendor.get("email") or "").strip() or None,
        "phone": (vendor.get("phone") or "").strip() or None,
        "country": "Canada",
        "notes": (f"Address: {(vendor.get('address') or '').strip()}"
                  if vendor.get("address") else None) or "Added via AI invoice intake",
    }).execute()
    return res.data[0]


def _gen_sku(supabase: Client) -> str:
    for _ in range(6):
        sku = "JS-AI-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        hit = supabase.table("products").select("id").eq("sku", sku).limit(1).execute().data
        if not hit:
            return sku
    return "JS-AI-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def _unique_bill_number(supabase: Client, base: str) -> str:
    base = (base or "").strip() or "INV"
    cand, i = base, 2
    while True:
        hit = supabase.table("bills").select("id").eq("bill_number", cand).limit(1).execute().data
        if not hit:
            return cand
        cand, i = f"{base}-{i}", i + 1


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class VendorIn(BaseModel):
    name: str
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None


class InvoiceItemIn(BaseModel):
    sku: str | None = None
    description: str
    quantity: float = 1
    unit: str | None = "each"
    unit_price: float = 0


class InvoiceConfirmIn(BaseModel):
    vendor: VendorIn
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    line_items: list[InvoiceItemIn] = []
    subtotal: float = 0
    tax: float = 0
    shipping: float = 0
    total: float = 0
    source_filename: str | None = None


class UrlIn(BaseModel):
    url: str


class ProductConfirmIn(BaseModel):
    name: str
    description: str | None = None
    sku: str | None = None
    category_id: str | None = None
    supplier_name: str | None = None
    unit: str | None = "each"
    cost_price: float | None = 0
    sell_price: float | None = None  # defaults to cost + 38%
    quantity: float = 0


# ---------------------------------------------------------------------------
# Invoice: analyze (read-only)
# ---------------------------------------------------------------------------
def _pdf_to_images(raw: bytes) -> list[str]:
    """Render first 3 PDF pages to PNG data URLs for the vision model."""
    doc = fitz.open(stream=raw, filetype="pdf")
    urls = []
    for page in doc[:3]:
        pix = page.get_pixmap(dpi=150)
        b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
        urls.append(f"data:image/png;base64,{b64}")
    doc.close()
    if not urls:
        raise HTTPException(400, "Could not read any page from this PDF")
    return urls


@router.post("/analyze-invoice")
async def analyze_invoice(file: UploadFile = File(...)):
    raw = await file.read()
    ctype = (file.content_type or "").lower()
    fname = (file.filename or "").lower()
    is_pdf = "pdf" in ctype or fname.endswith(".pdf")

    if is_pdf:
        if len(raw) > MAX_PDF_BYTES:
            raise HTTPException(413, "PDF too large — 15 MB max")
        try:
            images = _pdf_to_images(raw)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"Could not read PDF: {exc}")
        # Also grab any embedded text as extra context for the model.
        try:
            doc = fitz.open(stream=raw, filetype="pdf")
            text_ctx = "\n".join(p.get_text() for p in doc[:3])[:6000]
            doc.close()
        except Exception:
            text_ctx = ""
    else:
        if ctype not in ("image/jpeg", "image/png", "image/webp") \
                and not fname.endswith((".jpg", ".jpeg", ".png", ".webp")):
            raise HTTPException(400, "Use a PDF, JPG, PNG or WEBP invoice")
        if len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Image too large — 6 MB max")
        b64 = base64.b64encode(raw).decode("ascii")
        mime = ctype if ctype.startswith("image/") else "image/jpeg"
        images = [f"data:{mime};base64,{b64}"]
        text_ctx = ""

    user_text = (
        "Extract every field from this vendor/supplier invoice and return ONLY "
        "valid JSON matching this schema (use empty strings / 0 where unknown):\n"
        + INVOICE_SCHEMA_HINT
        + ("\n\nEmbedded PDF text for reference:\n" + text_ctx if text_ctx else "")
    )
    content: list[dict] = [{"type": "text", "text": user_text}]
    for img in images:
        content.append({"type": "image_url", "image_url": {"url": img}})
    raw_out = await _nvidia_chat(
        [{"role": "system",
          "content": "You extract structured data from vendor invoices. "
                     "Return ONLY valid JSON, no markdown, no commentary."},
         {"role": "user", "content": content}],
        max_tokens=3072)
    data = _clean_json(raw_out)
    data["source_filename"] = file.filename
    return data


# ---------------------------------------------------------------------------
# Invoice: confirm (writes supplier + products + bill)
# ---------------------------------------------------------------------------
@router.post("/confirm-invoice")
def confirm_invoice(body: InvoiceConfirmIn, supabase: Client = Depends(get_supabase)):
    if not body.line_items:
        raise HTTPException(400, "No line items to create")

    supplier = _find_or_create_supplier(supabase, body.vendor.model_dump())
    created, updated = [], []
    for it in body.line_items:
        desc = (it.description or "").strip()
        if not desc:
            continue
        qty = it.quantity or 0
        cost = round(it.unit_price or 0, 2)
        sell = round(cost * MARKUP, 2)
        sku = (it.sku or "").strip() or _gen_sku(supabase)
        unit = _map_unit(it.unit)

        existing = supabase.table("products").select("id,quantity") \
            .eq("sku", sku).limit(1).execute().data
        payload = {
            "sku": sku,
            "name": desc[:120],
            "description": desc,
            "unit": unit,
            "cost_price": cost,
            "sell_price": sell,
            "supplier_id": supplier["id"],
            "show_price": False,
            "is_active": True,
        }
        if existing:
            old_qty = existing[0].get("quantity") or 0
            payload["quantity"] = round(old_qty + qty, 2)
            supabase.table("products").update(payload).eq("id", existing[0]["id"]).execute()
            updated.append({"sku": sku, "name": desc[:60], "cost": cost, "sell": sell})
        else:
            payload["quantity"] = round(qty, 2)
            supabase.table("products").insert(payload).execute()
            created.append({"sku": sku, "name": desc[:60], "cost": cost, "sell": sell})

    bill_number = _unique_bill_number(supabase, body.invoice_number or "INV")
    total = round(body.total or 0, 2)
    bill = supabase.table("bills").insert({
        "bill_number": bill_number,
        "supplier_id": supplier["id"],
        "status": "unpaid",
        "issue_date": _parse_date(body.invoice_date),
        "due_date": _parse_date(body.due_date),
        "subtotal": round(body.subtotal or 0, 2),
        "gst": round(body.tax or 0, 2),
        "total": total,
        "balance_due": total,
        "notes": f"AI invoice intake from {body.source_filename or 'upload'}"
                 f" (vendor ref: {body.invoice_number or 'n/a'})",
    }).execute().data[0]

    return {
        "supplier": {"id": supplier["id"], "name": supplier["name"]},
        "products_created": created,
        "products_updated": updated,
        "bill": {"id": bill["id"], "bill_number": bill["bill_number"],
                 "total": bill["total"], "status": bill["status"]},
        "markup": "38%",
    }


# ---------------------------------------------------------------------------
# Product from website URL (analyze = read-only)
# ---------------------------------------------------------------------------
@router.post("/analyze-url")
async def analyze_url(body: UrlIn):
    url = (body.url or "").strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "Paste a full http(s) product URL")
    http = await get_http()
    try:
        resp = await http.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0 Safari/537.36"},
            timeout=25.0, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        raise HTTPException(502, f"Could not fetch that page: {exc}")

    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ",
                  html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:12000]
    if len(text) < 100:
        raise HTTPException(502, "That page had no readable product text")

    prompt = (
        "Extract the product from this web page text and return ONLY valid JSON "
        'like {"name": "", "description": "", "price": 0.0, "currency": "CAD", '
        '"sku": "", "brand": ""}. Use 0.0 for price if none is shown.\n\n'
        + text)
    raw_out = await _nvidia_chat(
        [{"role": "system",
          "content": "You extract product data from web pages. "
                     "Return ONLY valid JSON, no markdown, no commentary."},
         {"role": "user", "content": prompt}],
        max_tokens=1024)
    data = _clean_json(raw_out)
    data["source_url"] = url
    return data


# ---------------------------------------------------------------------------
# Product from photo (analyze = read-only)
# ---------------------------------------------------------------------------
@router.post("/analyze-photo")
async def analyze_photo(image: UploadFile = File(...)):
    raw = await image.read()
    ctype = (image.content_type or "").lower()
    if ctype not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "Use jpg, png, or webp")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image too large — 6 MB max")
    b64 = base64.b64encode(raw).decode("ascii")
    raw_out = await _nvidia_chat(
        [{"role": "system",
          "content": "You identify products from photos. "
                     "Return ONLY valid JSON, no markdown, no commentary."},
         {"role": "user", "content": [
             {"type": "text",
              "text": 'Identify this product and return ONLY valid JSON like '
                      '{"name": "", "description": "", '
                      '"suggested_unit": "each"}. The name should be a short '
                      'catalog-style product name; description 1-2 sentences '
                      'suitable for a hotel-supply catalog.'},
             {"type": "image_url",
              "image_url": {"url": f"data:{ctype};base64,{b64}"}},
         ]}],
        max_tokens=768)
    return _clean_json(raw_out)


# ---------------------------------------------------------------------------
# Product: confirm (writes supplier? + product)
# ---------------------------------------------------------------------------
@router.post("/confirm-product")
def confirm_product(body: ProductConfirmIn, supabase: Client = Depends(get_supabase)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Product name is required")

    supplier_id = None
    supplier_name = (body.supplier_name or "").strip()
    if supplier_name:
        s = _find_or_create_supplier(supabase, {"name": supplier_name})
        supplier_id = s["id"]

    sku = (body.sku or "").strip() or _gen_sku(supabase)
    cost = round(body.cost_price or 0, 2)
    sell = round(body.sell_price, 2) if body.sell_price else round(cost * MARKUP, 2)

    existing = supabase.table("products").select("id").eq("sku", sku).limit(1).execute().data
    payload = {
        "sku": sku,
        "name": name[:120],
        "description": (body.description or "").strip() or None,
        "category_id": body.category_id or None,
        "supplier_id": supplier_id,
        "unit": _map_unit(body.unit),
        "cost_price": cost,
        "sell_price": sell,
        "quantity": round(body.quantity or 0, 2),
        "show_price": False,
        "is_active": True,
    }
    if existing:
        supabase.table("products").update(payload).eq("id", existing[0]["id"]).execute()
        action = "updated"
    else:
        supabase.table("products").insert(payload).execute()
        action = "created"
    return {"action": action, "sku": sku, "name": name[:60],
            "cost": cost, "sell": sell, "markup": "38%"}
