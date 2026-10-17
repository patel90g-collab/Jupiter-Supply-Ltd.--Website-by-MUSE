"""
Jupiter Supply Ltd. — AI Product Search Engine.

Endpoints:
  GET  /search/keyword   ?q=...          keyword / model-number search over catalog
  POST /search/image                         product photo -> AI identification + catalog matches
  GET  /search/suppliers ?product=...    wholesale supplier finder (MANDATORY ad-filter)

HARD RULES (from Gaurav, 2026-10-16):
  1. Never expose supplier_id, cost_price, or margins via any endpoint.
  2. /search/suppliers MUST exclude paid-advertising consumer marketplaces.
     They buy top Google placement but are consumer sellers, not real
     wholesalers. The MANDATORY_BLOCKLIST below is enforced in code —
     blocked domains are dropped BEFORE results are returned, and the
     response reports how many were filtered.
  3. Nothing here sends email or quotes, so the YES-gate is not triggered
     by search. Any future send endpoint must call require_approval().
"""
from __future__ import annotations

import base64
import json
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from supabase import Client

from deps import (
    Settings,
    get_http,
    get_settings,
    get_supabase,
    log_activity,
    nvidia_headers,
    public_product,
)

router = APIRouter(prefix="/search", tags=["search"])

# ---------------------------------------------------------------------------
# MANDATORY AD-FILTER — Gaurav's hard rule (2026-10-16).
# These domains are paid-advertising CONSUMER marketplaces. They pay Google
# for top placement but are not real wholesale suppliers. They must NEVER
# appear in /search/suppliers results. Matching is by domain suffix, so
# subdomains (e.g. www.amazon.ca, smile.amazon.com) are also blocked.
# To add a domain: append its bare domain to the tuple below.
# ---------------------------------------------------------------------------
MANDATORY_BLOCKLIST: tuple[str, ...] = (
    "amazon.com", "amazon.ca", "amazon.co.uk", "amazon.in", "amazon.ae",
    "amazon.com.au", "amazon.de", "amazon.fr", "amazon.co.jp",
    "walmart.com", "walmart.ca",
    "wayfair.com", "wayfair.ca", "wayfair.co.uk",
    "ebay.com", "ebay.ca", "ebay.co.uk",
    "etsy.com",
    "aliexpress.com", "aliexpress.us",
    "target.com",
    "costco.com", "costco.ca",
    "homedepot.com", "homedepot.ca",
    "lowes.com", "lowes.ca",
    "bestbuy.com", "bestbuy.ca",
    "temu.com",
    "shein.com",
    "wish.com",
    "overstock.com",
    "bedbathandbeyond.com",
)

# Preferred B2B / wholesale / manufacturer sources — shown first when present.
# (Boost only; never a substitute for the blocklist.)
B2B_PREFERRED: tuple[str, ...] = (
    "alibaba.com",            # B2B wholesale marketplace
    "made-in-china.com",      # B2B manufacturer directory
    "globalsources.com",      # B2B sourcing
    "thomasnet.com",          # North American industrial suppliers
    "globalspec.com",
    "dhgate.com",             # B2B wholesale (retail listings still filtered by query)
    "tradeindia.com",
    "indiamart.com",
)


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def is_blocked(url: str) -> bool:
    """True when the URL belongs to a MANDATORY-blocked consumer marketplace."""
    d = _domain(url)
    return any(d == b or d.endswith("." + b) for b in MANDATORY_BLOCKLIST)


def is_preferred_b2b(url: str) -> bool:
    d = _domain(url)
    return any(d == b or d.endswith("." + b) for b in B2B_PREFERRED)


def apply_mandatory_filter(results: list[dict]) -> tuple[list[dict], int]:
    """
    Drop every result from a blocked domain. Returns (kept, dropped_count).
    `results` items need at least a "url" key; optional "is_sponsored" flag
    from the search provider also triggers a drop.
    """
    kept: list[dict] = []
    dropped = 0
    for r in results:
        url = r.get("url", "")
        if not url or is_blocked(url) or r.get("is_sponsored"):
            dropped += 1
            continue
        kept.append(r)
    # Preferred B2B sources float to the top; original order otherwise kept.
    kept.sort(key=lambda r: 0 if is_preferred_b2b(r.get("url", "")) else 1)
    return kept, dropped


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class KeywordResult(BaseModel):
    query: str
    count: int
    products: list[dict] = Field(default_factory=list)


class ImageIdentification(BaseModel):
    name: str = ""
    brand: str = ""
    model_number: str = ""
    category: str = ""
    key_features: list[str] = Field(default_factory=list)
    confidence: str = ""  # high | medium | low


class ImageSearchResult(BaseModel):
    identification: ImageIdentification
    catalog_matches: list[dict] = Field(default_factory=list)
    note: str = ""


class SupplierHit(BaseModel):
    title: str
    url: str
    snippet: str = ""
    domain: str = ""
    b2b_preferred: bool = False


class SupplierResult(BaseModel):
    product: str
    count: int
    blocked_filtered: int  # how many paid-ad consumer results were removed
    suppliers: list[SupplierHit] = Field(default_factory=list)
    disclaimer: str = (
        "Results are wholesale/B2B candidates only. Paid-advertising consumer "
        "marketplaces are excluded by policy. Verify any supplier before ordering."
    )


# ---------------------------------------------------------------------------
# GET /search/keyword
# ---------------------------------------------------------------------------
@router.get("/keyword", response_model=KeywordResult)
async def keyword_search(
    q: str = Query(..., min_length=2, max_length=120,
                   description="Model number, SKU, or item name"),
    limit: int = Query(25, ge=1, le=100),
    supabase: Client = Depends(get_supabase),
):
    """
    Keyword search over the catalog. Matches SKU, name, and description
    (case-insensitive). Reads the products_public VIEW only, so supplier
    internals can never leak — then public_product() strips once more.
    """
    pattern = f"%{q.strip()}%"
    try:
        res = (
            supabase.table("products_public")
            .select("id,sku,name,description,category_id,unit,sell_price,"
                    "show_price,is_active")
            .or_(f"name.ilike.{pattern},sku.ilike.{pattern},"
                 f"description.ilike.{pattern}")
            .eq("is_active", True)
            .order("name")
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Catalog search failed: {exc}",
        )
    products = [public_product(r) for r in (res.data or [])]
    await log_activity(supabase, "search.keyword",
                       details={"q": q, "count": len(products)})
    return KeywordResult(query=q, count=len(products), products=products)


# ---------------------------------------------------------------------------
# POST /search/image
# ---------------------------------------------------------------------------
VISION_SYSTEM_PROMPT = """You are a wholesale hotel-supply product identifier.
Given a product photo, identify it as JSON with EXACTLY these keys:
{
  "name": "plain product name, e.g. 'White Bath Towel'",
  "brand": "visible brand or empty string",
  "model_number": "visible model/SKU or empty string",
  "category": "best fit: Guest Amenities | Housekeeping | Room Supplies | Bathroom | "
              "Operations | Furniture | Appliances | Bed & Bath Linens | Towels | "
              "Janitorial | Hotel Slippers | Healthcare Textiles",
  "key_features": ["up to 5 short observable features: color, material, size cues"],
  "confidence": "high | medium | low"
}
Return ONLY the JSON object, no markdown, no commentary."""

MAX_IMAGE_BYTES = 6 * 1024 * 1024  # 6 MB
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


@router.post("/image", response_model=ImageSearchResult)
async def image_search(
    image: UploadFile = File(..., description="Product photo (jpg/png/webp)"),
    supabase: Client = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
):
    """
    1. Accept a product photo (from text/WhatsApp/email upload).
    2. NVIDIA vision model identifies the product -> structured JSON.
    3. The identified name/model number is run through keyword_search
       to find matches already in the Jupiter catalog.
    """
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Use jpg, png, or webp (got {image.content_type}).",
        )
    raw = await image.read()
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Image too large — 6 MB max.",
        )
    b64 = base64.b64encode(raw).decode("ascii")
    data_url = f"data:{image.content_type};base64,{b64}"

    payload = {
        "model": settings.nvidia_vision_model,
        "temperature": 0.1,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": "Identify this product as JSON."},
                    {"type": "image_url",
                     "image_url": {"url": data_url}},
                ],
            },
        ],
    }

    http = await get_http()
    try:
        resp = await http.post(
            f"{settings.nvidia_base_url}/chat/completions",
            headers=nvidia_headers(settings),
            json=payload,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Vision model error: {exc.response.status_code}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Vision model unreachable: {exc}",
        )

    # Parse the model's JSON (tolerate stray markdown fences and surrounding chatter).
    cleaned = re.sub(r"^```(?:json)?|```$", "", content.strip(),
                     flags=re.MULTILINE).strip()
    # If the model wrapped JSON in prose, extract the first {...} block.
    if not cleaned.startswith("{"):
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        cleaned = m.group(0) if m else cleaned
    try:
        ident = ImageIdentification(**json.loads(cleaned))
    except Exception:
        ident = ImageIdentification(
            name=content[:200], confidence="low",
        )

    # Match against the catalog using the identified name / model number.
    catalog_matches: list[dict] = []
    note = ""
    for term in (ident.model_number, ident.name):
        if term and len(term) >= 2:
            kw = await keyword_search(q=term, limit=10, supabase=supabase)
            for p in kw.products:
                if p["id"] not in {m["id"] for m in catalog_matches}:
                    catalog_matches.append(p)
            if catalog_matches:
                break
    if not catalog_matches:
        note = ("No catalog match found — use /search/suppliers to hunt "
                "for a wholesale supplier.")

    await log_activity(
        supabase, "search.image",
        details={"name": ident.name, "model_number": ident.model_number,
                 "confidence": ident.confidence,
                 "catalog_matches": len(catalog_matches)},
    )
    return ImageSearchResult(
        identification=ident, catalog_matches=catalog_matches, note=note
    )


# ---------------------------------------------------------------------------
# GET /search/suppliers
# ---------------------------------------------------------------------------
@router.get("/suppliers", response_model=SupplierResult)
async def supplier_search(
    product: str = Query(..., min_length=3, max_length=200,
                         description="Product name / model number"),
    limit: int = Query(10, ge=1, le=25),
    supabase: Client = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
):
    """
    Wholesale supplier finder. Searches the web for "<product> wholesale
    supplier manufacturer", then applies the MANDATORY ad-filter:
    paid-advertising consumer marketplaces are dropped before the
    response is built. The response reports how many were filtered.
    """
    if not settings.search_api_key:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Web search is not configured (SEARCH_API_KEY missing).",
        )

    query = f"{product.strip()} wholesale supplier manufacturer bulk"
    http = await get_http()
    try:
        # Serper.dev: POST with X-API-KEY header, organic[] results
        # with title/link/snippet. Free tier, no card.
        resp = await http.post(
            settings.search_api_url,
            headers={"X-API-KEY": settings.search_api_key,
                     "Content-Type": "application/json"},
            json={"q": query, "num": max(limit * 3, 20)},
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Supplier search failed: {exc.response.status_code}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Supplier search unreachable: {exc}",
        )

    raw_results: list[dict] = []
    for item in data.get("organic", []):
        raw_results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                # Serper doesn't flag ads; the MANDATORY domain blocklist
                # below is the primary defense.
                "is_sponsored": False,
            }
        )

    kept, dropped = apply_mandatory_filter(raw_results)
    hits = [
        SupplierHit(
            title=r["title"][:160],
            url=r["url"],
            snippet=r["snippet"][:300],
            domain=_domain(r["url"]),
            b2b_preferred=is_preferred_b2b(r["url"]),
        )
        for r in kept[:limit]
    ]

    await log_activity(
        supabase, "search.suppliers",
        details={"product": product, "returned": len(hits),
                 "blocked_filtered": dropped},
    )
    return SupplierResult(
        product=product, count=len(hits), blocked_filtered=dropped,
        suppliers=hits,
    )
