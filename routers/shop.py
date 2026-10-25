"""
Jupiter Supply Ltd. — Live storefront (public, no login).

Reads the products_public VIEW on every request, so anything added in the
admin (dashboard Products form or AI Intake) appears here automatically,
grouped by category. Prices show ONLY for products with show_price=true
(the view returns NULL otherwise) — supplier, cost and margin data never
leave the database.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from supabase import Client

from deps import get_supabase

router = APIRouter(tags=["shop"])

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:#0f1f3d;color:#fff;min-height:100vh;padding:20px}
.wrap{max-width:900px;margin:0 auto}
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
"""

FOOT = """<div class="foot">Jupiter Supply Ltd. · Supplying Quality. Delivering Trust.<br>
<a href="mailto:sales@jupitersupply.ca">sales@jupitersupply.ca</a> · Cell 587.778.1702 · Office 587.778.3633<br>
151-8745 165 St NW, Edmonton, AB T5R 2R7 · <a href="https://wa.me/15877781702">Chat on WhatsApp</a></div>"""


def _money(n) -> str:
    return "$" + f"{float(n or 0):,.2f}"


def _page(title: str, body: str) -> HTMLResponse:
    html = ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{title} — Jupiter Supply Ltd.</title>"
            f"<style>{CSS}</style></head><body><div class=\"wrap\">"
            "<div class=\"top\"><div class=\"logo\">🪐 JUPITER <span>SUPPLY</span></div>"
            "<span class=\"badge\">LIVE CATALOG</span></div>"
            + body + FOOT + "</div></body></html>")
    return HTMLResponse(html)


@router.get("/shop", include_in_schema=False)
def shop_home(supabase: Client = Depends(get_supabase)):
    cats = supabase.table("categories").select("id,name,slug") \
        .order("sort_order").execute().data or []
    prods = supabase.table("products_public").select("category_id") \
        .eq("is_active", True).limit(2000).execute().data or []
    counts: dict[str, int] = {}
    for p in prods:
        counts[p["category_id"]] = counts.get(p["category_id"], 0) + 1
    cards = ""
    for c in cats:
        n = counts.get(c["id"], 0)
        cards += (f"<div class=\"card\"><h2>{c['name']}</h2>"
                  f"<p>{n} product{'s' if n != 1 else ''}</p>"
                  f"<a class=\"btn\" href=\"/shop/{c['slug']}\">Browse →</a></div>")
    body = ("<h1>🛍️ Shop by Category</h1>"
            "<p class=\"sub\">Live from the Jupiter catalog — new products appear "
            "here automatically as soon as they are added. Prices are shown "
            "only where approved; everything else is Request Quote.</p>"
            f"<div class=\"grid\">{cards}</div>")
    return _page("Shop", body)


@router.get("/shop/{slug}", include_in_schema=False)
def shop_category(slug: str, supabase: Client = Depends(get_supabase)):
    cats = supabase.table("categories").select("id,name,slug") \
        .eq("slug", slug).limit(1).execute().data
    if not cats:
        return _page("Not found",
                     "<a class=\"back\" href=\"/shop\">← All categories</a>"
                     "<h1>Category not found</h1>"
                     "<p class=\"sub\">That category does not exist.</p>")
    cat = cats[0]
    prods = supabase.table("products_public") \
        .select("sku,name,description,unit,sell_price,show_price") \
        .eq("category_id", cat["id"]).eq("is_active", True) \
        .order("name").limit(500).execute().data or []
    if not prods:
        cards = ("<p class=\"sub\">No products in this category yet — "
                 "check back soon, new arrivals are added regularly.</p>")
    else:
        cards = ""
        for p in prods:
            price = (_money(p["sell_price"])
                     if (p.get("show_price") and p.get("sell_price") is not None)
                     else None)
            price_html = (f"<div class=\"price\">{price} <span style=\"font-size:12px;"
                          f"color:#9db2d8\">/ {p['unit']}</span></div>"
                          if price else "<span class=\"tag\">Request Quote</span>")
            desc = (p.get("description") or "")[:180]
            wa = ("https://wa.me/15877781702?text="
                  f"Hi Jupiter Supply, I am interested in {p['name']} ({p['sku']}).")
            cards += (f"<div class=\"card\"><h2>{p['name']}</h2>"
                      f"<p>{desc}</p>"
                      f"<p style=\"color:#9db2d8;font-size:12px\">SKU: {p['sku']} · {p['unit']}</p>"
                      f"{price_html}<br>"
                      f"<a class=\"btn\" href=\"{wa}\">Order on WhatsApp</a></div>")
    body = (f"<a class=\"back\" href=\"/shop\">← All categories</a>"
            f"<h1>{cat['name']}</h1>"
            f"<p class=\"sub\">{len(prods)} product{'s' if len(prods) != 1 else ''}</p>"
            f"<div class=\"grid\">{cards}</div>")
    return _page(cat["name"], body)
