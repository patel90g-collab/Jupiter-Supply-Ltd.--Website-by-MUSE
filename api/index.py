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

app = FastAPI(
    title="Jupiter Supply — AI Product Search Engine",
    version="0.1.0",
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


@app.get("/health")
async def health():
    return {"ok": True, "service": "jupiter-search", "version": "0.1.0"}


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
.ok{color:#7CFC98}.wait{color:#D4A017}
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
</div><script>
async function call(path, opts, outId, statusId){
  const out=document.getElementById(outId), st=document.getElementById(statusId);
  out.style.display='block'; st.innerHTML='<span class="wait">⏳ working…</span>'; out.textContent='';
  try{
    const r=await fetch(path,opts);
    const ct=r.headers.get('content-type')||'';
    if(!r.ok || !ct.includes('json')){
      st.innerHTML='<span class="wait">⚠️ search needs its API keys first — paste them in Vercel → Settings → Environment Variables, then try again.</span>';
      out.textContent=''; out.style.display='none'; return;
    }
    const j=await r.json();
    out.textContent=JSON.stringify(j,null,2);
    st.innerHTML='<span class="ok">✅ done</span>';
  }catch(e){ st.innerHTML='<span class="wait">⚠️ '+e.message+'</span>'; }
}
function kwSearch(){ const q=document.getElementById('kw').value.trim(); if(!q) return;
  call('/search/keyword?q='+encodeURIComponent(q),{},'kwOut','kwStatus'); }
function supSearch(){ const q=document.getElementById('sup').value.trim(); if(!q) return;
  call('/search/suppliers?product='+encodeURIComponent(q),{},'supOut','supStatus'); }
function imgSearch(){
  const f=document.getElementById('imgFile').files[0];
  const out=document.getElementById('imgOut'), st=document.getElementById('imgStatus');
  if(!f){ st.innerHTML='<span class="wait">⚠️ pick a photo first</span>'; return; }
  out.style.display='block'; st.innerHTML='<span class="wait">⏳ identifying…</span>';
  const fd=new FormData(); fd.append('image',f,f.name||'photo.jpg');
  call('/search/image',{method:'POST',body:fd},'imgOut','imgStatus');
}
fetch('/health').then(r=>r.json()).then(j=>{ if(j.ok) document.querySelector('.sub').innerHTML+=' <span class="ok">● API online</span>'; });
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
