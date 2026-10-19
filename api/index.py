"""
Jupiter Supply Ltd. — AI Product Search Engine API.

Run locally:
    uvicorn main:app --reload --port 8000
Docs:
    http://localhost:8000/docs

Env: copy .env.example to .env and fill in keys (never commit .env).
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import Client

from deps import get_settings, get_supabase, log_activity, require_approval
from routers.search import router as search_router
from routers.crud import router as crud_router
from routers.accounting import router as accounting_router
from routers.compliance import router as compliance_router
from routers.pos import router as pos_router
from routers.auth import router as auth_router

app = FastAPI(
    title="Jupiter Supply — AI Product Search Engine",
    version="0.1.1",
    description=(
        "Keyword + AI image product search and wholesale supplier finder "
        "for Jupiter Supply Ltd. Read-only search; every outbound send "
        "requires YES-gate approval."
    ),
)

settings = get_settings()
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

@app.get("/admin", include_in_schema=False)
async def admin_dashboard():
    from fastapi.responses import HTMLResponse
    import os
    p = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "admin_dashboard.html"))
    if not os.path.exists(p):
        p = os.path.join(os.path.dirname(__file__), "admin_dashboard.html")
    return HTMLResponse(open(p).read())

@app.get("/crm", include_in_schema=False)
async def crm_preview():
    from fastapi.responses import HTMLResponse
    import os
    p = os.path.join(os.path.dirname(__file__), "..", "crm_preview.html")
    p = os.path.normpath(p)
    if not os.path.exists(p):
        p = os.path.join(os.path.dirname(__file__), "crm_preview.html")
    return HTMLResponse(open(p).read())

app.include_router(search_router)
app.include_router(crud_router)
app.include_router(accounting_router)
app.include_router(compliance_router)
app.include_router(pos_router)
app.include_router(auth_router)


@app.get("/health")
async def health():
    return {"ok": True, "service": "jupiter-search", "version": "0.1.1"}





PREVIEW_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jupiter Supply — AI Search Engine (Preview)</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:#0f1f3d;color:#fff;min-height:100vh;padding:24px}
.wrap{max-width:720px;margin:0 auto}
.badge{display:inline-block;background:#D4A017;color:#0f1f3d;font-weight:700;font-size:12px;padding:4px 12px;border-radius:20px;letter-spacing:1px;margin-bottom:12px}
h1{font-size:28px;margin-bottom:6px}
.sub{color:#9db2d8;margin-bottom:24px;font-size:14px}
.card{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);border-radius:16px;padding:18px;margin-bottom:14px}
.card h2{font-size:17px;margin-bottom:6px}
.card p{color:#9db2d8;font-size:13px;margin-bottom:12px;line-height:1.5}
.row{display:flex;gap:8px}
input[type=text]{flex:1;padding:10px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.2);background:rgba(255,255,255,.08);color:#fff;font-size:14px}
button{padding:10px 18px;border-radius:10px;border:0;background:#D4A017;color:#0f1f3d;font-weight:700;font-size:14px;cursor:pointer}
button:disabled{opacity:.5}
pre{background:#0a1528;border-radius:10px;padding:12px;font-size:12px;overflow:auto;max-height:220px;margin-top:10px;color:#b8f0c0;white-space:pre-wrap}
.hint{font-size:12px;color:#9db2d8;margin-top:14px;text-align:center}
.hint a{color:#D4A017}
.status{font-size:12px;margin-top:8px}
.ok{color:#7CFC98}.wait{color:#D4A017}.err{color:#ff9a9a}
.res{background:#0a1528;border-radius:10px;padding:12px;margin-top:10px;font-size:13px}
.supcard{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:10px;margin-bottom:8px}
.supcard a{color:#D4A017;font-weight:700;font-size:14px;text-decoration:none}
.supcard .dom{color:#9db2d8;font-size:11px;margin:2px 0}
.supcard .snip{color:#cdd8ee;font-size:12px;line-height:1.4}
.prodcard{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:10px;margin-bottom:8px}
.prodcard .nm{font-weight:700;font-size:14px}
.prodcard .meta{color:#9db2d8;font-size:12px;margin-top:2px}
.idbox{background:rgba(212,160,23,.12);border:1px solid #D4A017;border-radius:10px;padding:10px;margin-bottom:8px;font-size:13px}
</style></head><body><div class="wrap">
<div class="badge">PRIVATE PREVIEW — NOT THE WEBSITE</div>
<h1>🔍 Jupiter AI Search Engine</h1>
<p class="sub">Try the three search features below. This is the backend preview — your website (jupitersupply.ca) is untouched.</p>
<div class="card"><h2>1️⃣ Keyword search</h2><p>Type a product name, SKU or model number. Searches your catalog.</p>
<div class="row"><input type="text" id="kw" placeholder="e.g. hotel towels"><button onclick="kwSearch()">Search</button></div>
<div class="status" id="kwStatus"></div><pre id="kwOut" style="display:none"></pre></div>
<div class="card"><h2>2️⃣ Photo search</h2><p>Pick a photo from your phone — AI identifies the product, then finds it in your catalog.</p>
<div class="row"><input type="file" id="imgFile" accept="image/*" style="flex:1;padding:8px;color:#9db2d8;font-size:13px"><button onclick="imgSearch()">Identify</button></div>
<div class="status" id="imgStatus"></div><pre id="imgOut" style="display:none"></pre></div>
<div class="card"><h2>3️⃣ Wholesale supplier finder</h2><p>Find real wholesale suppliers. Paid-ad sites are auto-blocked.</p>
<div class="row"><input type="text" id="sup" placeholder="e.g. hotel soap dispenser"><button onclick="supSearch()">Find suppliers</button></div>
<div class="status" id="supStatus"></div><pre id="supOut" style="display:none"></pre></div>
<p class="hint">Full technical docs: <a href="/docs">/docs</a> · Status: <a href="/health">/health</a></p>
</div>
<script>
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function setStatus(id,html){document.getElementById(id).innerHTML=html;}
function showOut(id,html){const o=document.getElementById(id);o.style.display='block';o.innerHTML=html;}
function hideOut(id){const o=document.getElementById(id);o.style.display='none';o.innerHTML='';}
async function fetchJSON(path,opts){
  const r=await fetch(path,opts);
  const ct=r.headers.get('content-type')||'';
  if(!r.ok){let msg='Search failed (HTTP '+r.status+')';try{const e=await r.json();if(e.detail)msg=msg+': '+e.detail;}catch(_){}throw new Error(msg);}
  if(!ct.includes('json'))throw new Error('Unexpected response from server');
  return r.json();
}
function renderKeyword(j){
  if(!j.products||!j.products.length)return '<div class="res">No products found for "'+esc(j.query)+'".</div>';
  let h='<div class="res"><b>'+j.products.length+' found:</b></div>';
  j.products.forEach(p=>{h+='<div class="prodcard"><div class="nm">'+esc(p.name)+'</div><div class="meta">SKU: '+esc(p.sku||'-')+(p.sell_price?' · $'+p.sell_price:'')+'</div></div>';});
  return h;
}
function renderSuppliers(j){
  let h='<div class="res"><b>'+j.count+' wholesale suppliers</b>'+(j.blocked_filtered?' <span class="ok">('+j.blocked_filtered+' paid-ad sites blocked)</span>':'')+'</div>';
  (j.suppliers||[]).forEach(s=>{h+='<div class="supcard"><a href="'+esc(s.url)+'" target="_blank" rel="noopener">'+esc(s.title)+'</a><div class="dom">'+esc(s.domain)+'</div><div class="snip">'+esc(s.snippet||'')+'</div></div>';});
  return h;
}
function renderImage(j){
  const idn=j.identification||{};
  let h='<div class="idbox"><b>Identified:</b> '+esc(idn.name||'unknown');
  if(idn.brand)h+=' · '+esc(idn.brand);
  if(idn.model_number)h+=' · Model: '+esc(idn.model_number);
  if(idn.confidence)h+=' <span class="ok">('+esc(idn.confidence)+' confidence)</span>';
  h+='</div>';
  if(j.catalog_matches&&j.catalog_matches.length){h+='<div class="res"><b>In your catalog:</b></div>';j.catalog_matches.forEach(p=>{h+='<div class="prodcard"><div class="nm">'+esc(p.name)+'</div><div class="meta">SKU: '+esc(p.sku||'-')+'</div></div>';});}
  else if(j.note)h+='<div class="res">'+esc(j.note)+'</div>';
  return h;
}
async function runSearch(path,opts,outId,statusId,render){
  setStatus(statusId,'<span class="wait">⏳ working…</span>');hideOut(outId);
  try{const j=await fetchJSON(path,opts);showOut(outId,render(j));setStatus(statusId,'<span class="ok">✅ done</span>');}
  catch(e){setStatus(statusId,'<span class="err">⚠️ '+esc(e.message)+'</span>');}
}
function kwSearch(){const q=document.getElementById('kw').value.trim();if(!q)return;
  runSearch('/search/keyword?q='+encodeURIComponent(q),{},'kwOut','kwStatus',renderKeyword);}
function supSearch(){const q=document.getElementById('sup').value.trim();if(!q)return;
  setStatus('supStatus','<span class="wait">⏳ searching wholesalers…</span>');hideOut('supOut');
  runSearch('/search/suppliers?product='+encodeURIComponent(q),{},'supOut','supStatus',renderSuppliers);}
function imgSearch(){
  const f=document.getElementById('imgFile').files[0];
  if(!f){setStatus('imgStatus','<span class="wait">⚠️ pick a photo first</span>');return;}
  setStatus('imgStatus','<span class="wait">⏳ identifying… (takes ~20s)</span>');hideOut('imgOut');
  const fd=new FormData();fd.append('image',f,f.name||'photo.jpg');
  runSearch('/search/image',{method:'POST',body:fd},'imgOut','imgStatus',renderImage);
}
fetch('/health').then(r=>r.json()).then(j=>{if(j.ok)document.querySelector('.sub').innerHTML+=' <span class="ok">● API online</span>';});
</script></body></html>"""


@app.get("/", include_in_schema=False)
async def preview_page():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(PREVIEW_HTML)


# ---------------------------------------------------------------------------
# YES-gate pattern demo: any endpoint that SENDS something externally must
# call require_approval() FIRST. Without an approved approvals row -> 403.
# Real senders (quote email, invoice email) will follow this exact shape.
# ---------------------------------------------------------------------------
class SendRequest(BaseModel):
    entity_type: str  # 'quote' | 'invoice' | 'email' | 'price_change' | 'order'
    entity_id: str
    to: str
    subject: str
    body: str


@app.post("/send/request", status_code=status.HTTP_202_ACCEPTED)
async def send_request(
    req: SendRequest,
    supabase: Client = Depends(get_supabase),
):
    """
    STUB demonstrating the YES-gate. Validates approval, then queues the
    send (actual provider wiring lands with the email provider decision).
    """
    approval = await require_approval(req.entity_type, req.entity_id, supabase)
    await log_activity(
        supabase,
        "send.queued",
        entity_type=req.entity_type,
        entity_id=req.entity_id,
        details={"to": req.to, "subject": req.subject,
                 "approval_id": approval["id"]},
    )
    # NOTE: real provider call (Resend/SMTP) goes here — only reachable
    # after the 403 gate above passes.
    return {
        "queued": True,
        "entity_type": req.entity_type,
        "entity_id": req.entity_id,
        "approval_id": approval["id"],
        "note": "Provider send not yet wired — queued only.",
    }


@app.on_event("shutdown")
async def _shutdown():
    from deps import _http_client

    if _http_client is not None:
        await _http_client.aclose()
