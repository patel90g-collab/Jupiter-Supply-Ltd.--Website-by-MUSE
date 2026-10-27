"""
Jupiter Supply Ltd. — Live storefront (public, no login).

Reads the products_public VIEW + categories on every request, so anything
added in the admin (dashboard Products form or AI Intake) appears here
automatically, grouped by category. NOTE: preview deployments need
SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in the Preview env scope
(Vercel project settings) or every shop route returns 500.
Prices show ONLY for products with
show_price=true (the view returns NULL otherwise) — supplier, cost and
margin data never leave the database.

Features:
- /shop — category cards with live product counts + SKU search box.
- /shop/{slug} — products for one category, paginated (100/page), with a
  category tab bar to switch categories without going back.
- /shop/search?q= — SKU + name search across ALL products.
- Product images: uses image_url when present (populated from the
  products table); otherwise shows a clean placeholder. Never breaks
  when image_url is absent (e.g. before the column is populated).
- Request Quote: shown whenever show_price is false or sell_price is
  NULL — which covers the entire new catalog (all show_price=false).
"""

from __future__ import annotations

import html
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from supabase import Client

from deps import get_supabase

router = APIRouter(tags=["shop"])

PAGE_SIZE = 100

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:#0f1f3d;color:#fff;min-height:100vh;padding:20px}
.wrap{max-width:1100px;margin:0 auto}
.top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.logo{font-weight:800;font-size:20px}.logo span{color:#D4A017}
.badge{background:#D4A017;color:#0f1f3d;font-size:11px;font-weight:700;padding:3px 10px;border-radius:12px}
a{color:#D4A017;text-decoration:none}
h1{font-size:26px;margin:14px 0 4px}
.sub{color:#9db2d8;font-size:14px;margin-bottom:20px;line-height:1.5}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.card{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);border-radius:14px;padding:16px}
.card h2{font-size:17px;margin-bottom:4px}
.card p{color:#9db2d8;font-size:13px;line-height:1.5;margin-bottom:10px}
.price{font-size:18px;font-weight:800;color:#fff}
.tag{display:inline-block;padding:2px 9px;border-radius:10px;font-size:11px;font-weight:600;background:#fdf3d8;color:#8a6d00}
.btn{display:inline-block;background:#D4A017;color:#0f1f3d;font-weight:700;font-size:13px;padding:8px 14px;border-radius:8px;margin-top:8px}
.btn.ghost{background:transparent;border:1px solid rgba(255,255,255,.3);color:#fff}
.foot{margin-top:26px;color:#9db2d8;font-size:12px;text-align:center;line-height:1.7}
.back{display:inline-block;margin-bottom:12px}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}
.tab{padding:7px 14px;border-radius:20px;border:1px solid rgba(255,255,255,.3);color:#fff;font-size:13px;font-weight:600}
.tab.active{background:#D4A017;border-color:#D4A017;color:#0f1f3d}
.searchbox{display:flex;gap:8px;margin:14px 0}
.searchbox input{flex:1;padding:10px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.25);background:rgba(255,255,255,.08);color:#fff;font-size:14px}
.searchbox button{padding:10px 18px;border-radius:10px;border:0;background:#D4A017;color:#0f1f3d;font-weight:700;font-size:14px;cursor:pointer}
.pimg{width:100%;height:160px;object-fit:contain;background:rgba(255,255,255,.06);border-radius:10px;margin-bottom:10px}
.ph{width:100%;height:160px;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,.06);border-radius:10px;margin-bottom:10px;font-size:44px}
.pager{display:flex;gap:10px;align-items:center;justify-content:center;margin-top:22px;flex-wrap:wrap}
.pager a,.pager span{padding:8px 14px;border-radius:8px;border:1px solid rgba(255,255,255,.3);font-size:13px;font-weight:600}
.pager a{color:#fff}
.pager span.cur{background:#D4A017;border-color:#D4A017;color:#0f1f3d}
.pager span.dis{opacity:.4}
"""

FOOT = """<div class="foot">Jupiter Supply Ltd. · Supplying Quality. Delivering Trust.<br>
<a href="mailto:sales@jupitersupply.ca">sales@jupitersupply.ca</a> · Cell 587.778.1702 · Office 587.778.3633<br>
151-8745 165 St NW, Edmonton, AB T5R 2R7 · <a href="https://wa.me/15877781702">Chat on WhatsApp</a></div>"""

COLS_WITH_IMG = "sku,name,description,unit,sell_price,show_price,image_url"
COLS_BASE = "sku,name,description,unit,sell_price,show_price"


def _money(n) -> str:
    return "$" + f"{float(n or 0):,.2f}"


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def _page(title: str, body: str) -> HTMLResponse:
    html_doc = ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                f"<title>{_esc(title)} — Jupiter Supply Ltd.</title>"
                f"<style>{CSS}</style></head><body><div class=\"wrap\">"
                "<div class=\"top\"><div class=\"logo\">🪐 JUPITER <span>SUPPLY</span></div>"
                "<span class=\"badge\">LIVE CATALOG</span></div>"
                + body + FOOT + "</div></body></html>")
    return HTMLResponse(html_doc)


def _tabs(cats, active_slug: str | None) -> str:
    """Category tab bar. active_slug=None highlights 'All Categories'."""
    parts = ['<div class="tabs">']
    cls = "tab active" if active_slug is None else "tab"
    parts.append(f'<a class="{cls}" href="/shop">All Categories</a>')
    for c in cats:
        cls = "tab active" if c["slug"] == active_slug else "tab"
        parts.append(f'<a class="{cls}" href="/shop/{_esc(c["slug"])}">{_esc(c["name"])}</a>')
    parts.append("</div>")
    return "".join(parts)


def _search_box(q: str = "") -> str:
    return (f'<form class="searchbox" action="/shop/search" method="get">'
            f'<input type="text" name="q" value="{_esc(q)}" placeholder="Search by SKU or product name…">'
            f'<button type="submit">Search</button></form>')


def _img_html(p: dict) -> str:
    """Product image when image_url is present, else a clean placeholder.

    Never breaks when image_url is absent (column not yet populated).
    """
    url = (p.get("image_url") or "").strip()
    if url:
        return f'<img class="pimg" src="{_esc(url)}" alt="{_esc(p.get("name", ""))}" loading="lazy">'
    return '<div class="ph">🪐</div>'


def _card_html(p: dict) -> str:
    price = (_money(p["sell_price"])
             if (p.get("show_price") and p.get("sell_price") is not None)
             else None)
    price_html = (f"<div class=\"price\">{price} <span style=\"font-size:12px;"
                  f"color:#9db2d8\">/ {p['unit']}</span></div>"
                  if price else "<span class=\"tag\">Request Quote</span>")
    desc = (p.get("description") or "")[:180]
    msg = quote(f"Hi Jupiter Supply, I am interested in {p['name']} ({p['sku']}).")
    wa = f"https://wa.me/15877781702?text={msg}"
    return (f"<div class=\"card\">{_img_html(p)}<h2>{_esc(p['name'])}</h2>"
            f"<p>{_esc(desc)}</p>"
            f"<p style=\"color:#9db2d8;font-size:12px\">SKU: {_esc(p['sku'])} · {_esc(p['unit'])}</p>"
            f"{price_html}<br>"
            f"<a class=\"btn\" href=\"{wa}\">Order on WhatsApp</a></div>")


def _pager_html(base: str, page: int, total_pages: int) -> str:
    if total_pages <= 1:
        return ""
    sep = "&" if "?" in base else "?"
    parts = ['<div class="pager">']
    if page > 1:
        parts.append(f'<a href="{base}{sep}page={page - 1}">← Prev</a>')
    else:
        parts.append('<span class="dis">← Prev</span>')
    parts.append(f'<span class="cur">Page {page} of {total_pages}</span>')
    if page < total_pages:
        parts.append(f'<a href="{base}{sep}page={page + 1}">Next →</a>')
    else:
        parts.append('<span class="dis">Next →</span>')
    parts.append("</div>")
    return "".join(parts)


def _fetch_cats(supabase: Client):
    return supabase.table("categories").select("id,name,slug") \
        .order("sort_order").order("name").execute().data or []


def _fetch_all_category_ids(supabase: Client) -> list:
    """All active product category_ids, paginated (no 2000-row cap)."""
    ids: list = []
    offset = 0
    while True:
        rows = supabase.table("products_public").select("category_id") \
            .eq("is_active", True).range(offset, offset + 999).execute().data or []
        ids.extend(r["category_id"] for r in rows if r.get("category_id"))
        if len(rows) < 1000:
            break
        offset += 1000
    return ids


def _fetch_page(build_query, page: int):
    """Fetch one page via build_query(cols). Tries WITH image_url first,
    falls back to WITHOUT it when the view lacks that column. Returns rows.
    build_query(cols) must return a query object ready for .range().execute().
    """
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE - 1
    try:
        res = build_query(COLS_WITH_IMG).range(start, end).execute()
        return res.data or []
    except Exception:
        res = build_query(COLS_BASE).range(start, end).execute()
        return res.data or []


def _count_exact(build_query) -> int:
    """Exact row count for build_query(cols). Returns 0 on any failure."""
    try:
        res = build_query("id", count="exact").limit(1).execute()
        if getattr(res, "count", None) is not None:
            return int(res.count)
    except Exception:
        pass
    return 0


@router.get("/shop", include_in_schema=False)
def shop_home(supabase: Client = Depends(get_supabase)):
    cats = _fetch_cats(supabase)
    ids = _fetch_all_category_ids(supabase)
    counts: dict[str, int] = {}
    for cid in ids:
        counts[cid] = counts.get(cid, 0) + 1
    total = len(ids)
    shown = [c for c in cats if counts.get(c["id"], 0) > 0]
    cards = ""
    for c in shown:
        n = counts.get(c["id"], 0)
        cards += (f"<div class=\"card\"><h2>{_esc(c['name'])}</h2>"
                  f"<p>{n} product{'s' if n != 1 else ''}</p>"
                  f"<a class=\"btn\" href=\"/shop/{_esc(c['slug'])}\">Browse →</a></div>")
    body = ("<h1>🛍️ Shop by Category</h1>"
            "<p class=\"sub\">Live from the Jupiter catalog — new products appear "
            "here automatically as soon as they are added. Prices are shown "
            "only where approved; everything else is Request Quote.</p>"
            + _search_box()
            + _tabs(cats, None)
            + f"<p class=\"sub\">{total} products across {len(shown)} categories.</p>"
            f"<div class=\"grid\">{cards}</div>")
    return _page("Shop", body)


@router.get("/shop/search", include_in_schema=False)
def shop_search(q: str = Query(""), page: int = Query(1, ge=1),
                supabase: Client = Depends(get_supabase)):
    cats = _fetch_cats(supabase)
    q = (q or "").strip()
    page = max(1, page)
    if not q:
        body = ("<a class=\"back\" href=\"/shop\">← All categories</a>"
                "<h1>Search</h1>"
                "<p class=\"sub\">Type a SKU or product name to search the catalog.</p>"
                + _search_box()
                + _tabs(cats, None))
        return _page("Search", body)
    # Sanitize for PostgREST or_() syntax (commas/parens would split conditions).
    safe_q = q.replace(",", " ").replace("(", " ").replace(")", " ").strip()
    like = f"%{safe_q}%"

    def build(cols, count=None):
        query = supabase.table("products_public").select(cols, count=count) \
            .or_(f"sku.ilike.{like},name.ilike.{like}") \
            .eq("is_active", True).order("name")
        return query

    rows = _fetch_page(lambda cols: build(cols), page)
    total = _count_exact(lambda cols, count=None: build(cols, count=count))
    if not total:
        total = len(rows)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if not rows:
        cards = (f"<p class=\"sub\">No products found for “{_esc(q)}” — "
                 f"try a different SKU or product name.</p>")
    else:
        cards = "".join(_card_html(p) for p in rows)
    body = (f"<a class=\"back\" href=\"/shop\">← All categories</a>"
            f"<h1>Search results</h1>"
            f"<p class=\"sub\">{total} result{'s' if total != 1 else ''} for “{_esc(q)}”</p>"
            + _search_box(q)
            + _tabs(cats, None)
            + f"<div class=\"grid\">{cards}</div>"
            + _pager_html(f"/shop/search?q={quote(q)}", page, total_pages))
    return _page("Search", body)


@router.get("/shop/{slug}", include_in_schema=False)
def shop_category(slug: str, page: int = Query(1, ge=1),
                  supabase: Client = Depends(get_supabase)):
    cats = _fetch_cats(supabase)
    match = [c for c in cats if c["slug"] == slug]
    if not match:
        return _page("Not found",
                     "<a class=\"back\" href=\"/shop\">← All categories</a>"
                     "<h1>Category not found</h1>"
                     "<p class=\"sub\">That category does not exist.</p>")
    cat = match[0]
    page = max(1, page)

    def build(cols, count=None):
        return supabase.table("products_public").select(cols, count=count) \
            .eq("category_id", cat["id"]).eq("is_active", True).order("name")

    rows = _fetch_page(lambda cols: build(cols), page)
    total = _count_exact(lambda cols, count=None: build(cols, count=count))
    if not total:
        total = len(rows)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if not rows:
        cards = ("<p class=\"sub\">No products in this category yet — "
                 "check back soon, new arrivals are added regularly.</p>")
    else:
        cards = "".join(_card_html(p) for p in rows)
    body = (f"<a class=\"back\" href=\"/shop\">← All categories</a>"
            f"<h1>{_esc(cat['name'])}</h1>"
            f"<p class=\"sub\">{total} product{'s' if total != 1 else ''}</p>"
            + _search_box()
            + _tabs(cats, slug)
            + f"<div class=\"grid\">{cards}</div>"
            + _pager_html(f"/shop/{_esc(slug)}", page, total_pages))
    return _page(cat["name"], body)
