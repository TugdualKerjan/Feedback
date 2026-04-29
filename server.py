"""
Markd — feedback annotation proxy server
"""
import os, re, secrets, contextlib
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────────────
DB      = os.environ.get("DB_PATH", "markd.db")
BASE    = os.environ.get("BASE_URL", "http://localhost:8000")
TIMEOUT = 15

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── DB ────────────────────────────────────────────────────────────────────────
def get_db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with contextlib.closing(get_db()) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                root_url    TEXT NOT NULL,
                created     TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS annotations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                url         TEXT NOT NULL,
                quote       TEXT NOT NULL,
                comment     TEXT NOT NULL,
                prefix      TEXT DEFAULT '',
                suffix      TEXT DEFAULT '',
                author      TEXT DEFAULT 'anonymous',
                created     TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
        """)
        con.commit()

init_db()

# ── Landing page ──────────────────────────────────────────────────────────────
LANDING = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Markd — get feedback on anything</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --ink: #111010; --paper: #f5f2eb; --amber: #e8a020;
    --muted: #7a7167; --border: #d6d0c4;
  }
  html, body { height: 100%; background: var(--paper); color: var(--ink); font-family: 'Instrument Serif', Georgia, serif; }
  body {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; padding: 2rem; min-height: 100vh;
    position: relative; overflow: hidden;
  }
  body::before {
    content: ''; position: fixed; inset: 0;
    background-image: linear-gradient(var(--border) 1px, transparent 1px),
                      linear-gradient(90deg, var(--border) 1px, transparent 1px);
    background-size: 48px 48px; opacity: .3; pointer-events: none;
  }
  .card { position: relative; z-index: 1; width: 100%; max-width: 520px; }
  .eyebrow { font-family: 'DM Mono', monospace; font-size: 11px; letter-spacing: .12em; text-transform: uppercase; color: var(--amber); margin-bottom: 1rem; }
  h1 { font-size: clamp(2.4rem, 6vw, 3.6rem); line-height: 1.05; font-weight: 400; margin-bottom: .6rem; }
  h1 em { font-style: italic; color: var(--amber); }
  .sub { font-size: 1.05rem; color: var(--muted); margin-bottom: 2.5rem; line-height: 1.5; }
  .input-row { display: flex; border: 1.5px solid var(--ink); border-radius: 10px; overflow: hidden; background: #fff; transition: box-shadow .2s; }
  .input-row:focus-within { box-shadow: 4px 4px 0 var(--ink); }
  .input-row input { flex: 1; border: none; outline: none; padding: 14px 16px; font-size: 15px; font-family: 'DM Mono', monospace; background: transparent; color: var(--ink); min-width: 0; }
  .input-row input::placeholder { color: #b0a898; }
  .input-row button { border: none; border-left: 1.5px solid var(--ink); background: var(--ink); color: var(--paper); padding: 14px 22px; font-size: 14px; font-family: 'Instrument Serif', serif; font-style: italic; cursor: pointer; white-space: nowrap; transition: background .15s; }
  .input-row button:hover { background: #2a2520; }
  .result { display: none; margin-top: 1.6rem; border: 1.5px solid var(--border); border-radius: 10px; padding: 16px 18px; background: #fff; }
  .result.show { display: block; }
  .result-label { font-family: 'DM Mono', monospace; font-size: 11px; text-transform: uppercase; letter-spacing: .1em; color: var(--muted); margin-bottom: 8px; }
  .result-link { font-family: 'DM Mono', monospace; font-size: 13px; color: var(--ink); word-break: break-all; cursor: pointer; border-bottom: 1px dashed var(--amber); }
  .copy-hint { font-size: 12px; color: var(--muted); margin-top: 8px; font-family: 'DM Mono', monospace; }
  .copy-hint.copied { color: #16a34a; }
  .footer { position: relative; z-index: 1; margin-top: 3rem; font-family: 'DM Mono', monospace; font-size: 11px; color: var(--border); text-align: center; }
</style>
</head>
<body>
<div class="card">
  <p class="eyebrow">✦ Markd</p>
  <h1>Collect feedback<br>on <em>any</em> website.</h1>
  <p class="sub">Paste a URL. Share the link.<br>Visitors highlight &amp; comment — you see it all.</p>
  <div class="input-row">
    <input type="url" id="url-input" placeholder="https://yoursite.com" autocomplete="off" spellcheck="false">
    <button id="go-btn">Give me feedback &rarr;</button>
  </div>
  <div class="result" id="result">
    <div class="result-label">Share this link</div>
    <div class="result-link" id="result-link" onclick="copyLink()"></div>
    <div class="copy-hint" id="copy-hint">click to copy</div>
  </div>
</div>
<p class="footer">highlight anything &middot; leave a comment &middot; no account needed</p>
<script>
const btn=document.getElementById('go-btn'),input=document.getElementById('url-input'),
      result=document.getElementById('result'),resultLink=document.getElementById('result-link'),
      copyHint=document.getElementById('copy-hint');
btn.addEventListener('click',generate);
input.addEventListener('keydown',e=>{if(e.key==='Enter')generate();});
async function generate(){
  let url=input.value.trim(); if(!url)return;
  if(!/^https?:\\/\\//.test(url))url='https://'+url;
  btn.textContent='Creating…'; btn.disabled=true;
  try{
    const res=await fetch('/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({root_url:url})});
    const {id}=await res.json();
    const link=`${window.location.origin}/r/${id}`;
    resultLink.textContent=link; result.classList.add('show');
    copyHint.textContent='click to copy'; copyHint.classList.remove('copied');
  }catch(e){alert('Error: '+e.message);}
  finally{btn.textContent='Give me feedback →'; btn.disabled=false;}
}
function copyLink(){
  navigator.clipboard.writeText(resultLink.textContent).then(()=>{
    copyHint.textContent='✓ copied!'; copyHint.classList.add('copied');
  });
}
</script>
</body>
</html>"""


# ── Overlay script injected into proxied pages ────────────────────────────────
def overlay_script(session_id: str, server_base: str, target_url: str) -> str:
    return f"""<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
#markd-badge{{position:fixed;bottom:24px;right:24px;z-index:2147483640;background:#111010;color:#f5f2eb;font-family:'Instrument Serif',Georgia,serif;font-style:italic;font-size:15px;padding:10px 18px;border-radius:100px;cursor:default;box-shadow:0 4px 20px rgba(0,0,0,.3);display:flex;align-items:center;gap:8px;user-select:none;animation:markd-in .4s cubic-bezier(.34,1.56,.64,1) both;}}
@keyframes markd-in{{from{{transform:translateY(20px);opacity:0}}to{{transform:translateY(0);opacity:1}}}}
#markd-dot{{width:8px;height:8px;background:#e8a020;border-radius:50%;animation:markd-pulse 2s ease-in-out infinite;}}
@keyframes markd-pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.5;transform:scale(.7)}}}}
*::selection{{background:rgba(232,160,32,.35)!important;color:inherit!important;}}
.markd-mark{{background:#fde68a!important;border-bottom:2px solid #e8a020!important;cursor:pointer!important;transition:background .12s;}}
.markd-mark:hover{{background:#fcd34d!important;}}
.markd-pop{{position:absolute;z-index:2147483638;background:#111010;color:#f5f2eb;border-radius:10px;padding:13px 15px;font-family:'Instrument Serif',Georgia,serif;font-size:14px;line-height:1.5;max-width:300px;min-width:180px;box-shadow:0 8px 32px rgba(0,0,0,.4);pointer-events:none;opacity:0;transform:translateY(8px);transition:opacity .15s,transform .15s;}}
.markd-pop.on{{opacity:1;transform:translateY(0);pointer-events:auto;}}
.markd-pop-comment{{font-style:italic;margin-bottom:5px;}}
.markd-pop-meta{{font-family:'DM Mono',monospace;font-size:11px;color:#a8a29e;}}
.markd-box{{position:fixed;z-index:2147483639;background:#f5f2eb;border:1.5px solid #111010;border-radius:12px;padding:16px;width:320px;box-shadow:4px 4px 0 #111010;font-family:'Instrument Serif',Georgia,serif;}}
.markd-qprev{{font-style:italic;font-size:13px;color:#7a7167;margin-bottom:12px;border-left:3px solid #e8a020;padding-left:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.markd-box textarea{{width:100%;border:1.5px solid #d6d0c4;border-radius:8px;padding:10px;font-size:14px;font-family:'Instrument Serif',Georgia,serif;font-style:italic;resize:vertical;min-height:80px;box-sizing:border-box;outline:none;background:#fff;color:#111010;}}
.markd-box textarea:focus{{border-color:#e8a020;}}
.markd-box input{{width:100%;border:1.5px solid #d6d0c4;border-radius:8px;padding:8px 10px;font-size:12px;font-family:'DM Mono',monospace;box-sizing:border-box;margin-top:8px;outline:none;background:#fff;color:#111010;}}
.markd-box input:focus{{border-color:#e8a020;}}
.markd-btns{{display:flex;gap:8px;margin-top:12px;justify-content:flex-end;}}
.markd-btn{{border:1.5px solid #111010;border-radius:8px;padding:7px 16px;font-size:13px;font-family:'Instrument Serif',Georgia,serif;font-style:italic;cursor:pointer;}}
.markd-cancel{{background:transparent;color:#7a7167;border-color:#d6d0c4;}}
.markd-submit{{background:#111010;color:#f5f2eb;}}
</style>
<script>
(function(){{
var S='{session_id}',B='{server_base}',PAGE='{target_url}';
var cache=[],box=null,pend=null;
var badge=document.createElement('div');
badge.id='markd-badge';
badge.innerHTML='<div id="markd-dot"></div>leave feedback';
document.body.appendChild(badge);
fetch(B+'/annotations?session='+S+'&url='+encodeURIComponent(PAGE)).then(r=>r.json()).then(d=>{{cache=d;d.forEach(render);}}).catch(()=>{{}});
function render(a){{var m=wrap(a.quote,a.id);if(m)tip(m,a);}}
function wrap(q,id){{
  var w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT,{{acceptNode:function(n){{
    var t=n.parentElement&&n.parentElement.tagName;
    if(['SCRIPT','STYLE','NOSCRIPT'].indexOf(t)>-1)return NodeFilter.FILTER_REJECT;
    if(n.parentElement&&n.parentElement.closest&&n.parentElement.closest('.markd-mark,.markd-box,#markd-badge'))return NodeFilter.FILTER_REJECT;
    return NodeFilter.FILTER_ACCEPT;
  }}}});
  var n;
  while((n=w.nextNode())){{
    var i=n.textContent.indexOf(q);if(i===-1)continue;
    var r=document.createRange();r.setStart(n,i);r.setEnd(n,i+q.length);
    var m=document.createElement('mark');m.className='markd-mark';m.dataset.id=id;
    try{{r.surroundContents(m);}}catch(e){{return null;}}
    return m;
  }}
  return null;
}}
function tip(m,a,showNow){{
  var p=document.createElement('div');p.className='markd-pop';
  p.innerHTML='<div class="markd-pop-comment">\u201c'+esc(a.comment)+'\u201d</div><div class="markd-pop-meta">\u2014 '+esc(a.author)+' \u00b7 '+fmt(a.created)+'</div>';
  document.body.appendChild(p);
  function reposition(){{var r=m.getBoundingClientRect();p.style.left=(r.left+scrollX)+'px';p.style.top=(r.bottom+scrollY+6)+'px';}}
  m.addEventListener('mouseenter',function(){{reposition();p.classList.add('on');}});
  m.addEventListener('mouseleave',function(e){{if(!p.contains(e.relatedTarget))p.classList.remove('on');}});
  p.addEventListener('mouseleave',function(){{p.classList.remove('on');}});
  if(showNow){{reposition();p.classList.add('on');}}
}}
document.addEventListener('mouseup',function(e){{
  if(box&&box.contains(e.target))return;
  if(badge.contains(e.target))return;
  var sel=getSelection(),q=sel&&sel.toString().trim();
  if(!q||q.length<3){{close();pend=null;return;}}
  var rng=sel.getRangeAt(0),rect=rng.getBoundingClientRect();
  var cn=rng.startContainer,full=cn.textContent||'';
  pend={{quote:q,prefix:full.slice(Math.max(0,rng.startOffset-32),rng.startOffset),suffix:full.slice(rng.endOffset,rng.endOffset+32)}};
  show(rect);
}});
function show(rect){{
  if(!pend)return;
  var pendQuote=pend.quote;
  close();box=document.createElement('div');box.className='markd-box';
  var top=rect.bottom+scrollY+10,left=Math.max(8,Math.min(rect.left+scrollX,innerWidth-336));
  box.style.top=top+'px';box.style.left=left+'px';
  box.innerHTML='<div class="markd-qprev">'+esc(pendQuote)+'</div><textarea placeholder="Your thought\u2026"></textarea><input type="text" placeholder="Your name (optional)"><div class="markd-btns"><button class="markd-btn markd-cancel">cancel</button><button class="markd-btn markd-submit">annotate \u2192</button></div>';
  document.body.appendChild(box);
  box.querySelector('textarea').focus();
  box.querySelector('.markd-cancel').addEventListener('click',close);
  box.querySelector('.markd-submit').addEventListener('click',submit);
  box.querySelector('textarea').addEventListener('keydown',function(e){{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey))submit();}});
}}
function close(){{if(box){{box.remove();box=null;}}}}
function submit(){{
  var comment=box.querySelector('textarea').value.trim();
  var author=box.querySelector('input').value.trim()||'anonymous';
  if(!comment){{box.querySelector('textarea').focus();return;}}
  var btn=box.querySelector('.markd-submit');btn.textContent='saving\u2026';btn.disabled=true;
  fetch(B+'/annotate',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify(Object.assign({{}},pend,{{session_id:S,url:PAGE,comment:comment,author:author}}))
  }}).then(r=>r.json()).then(function(d){{
    var a=Object.assign({{}},pend,{{id:d.id,comment:comment,author:author,created:new Date().toISOString()}});
    cache.push(a);close();pend=null;getSelection()&&getSelection().removeAllRanges();
    var m=wrap(a.quote,a.id);if(m)tip(m,a,true);
  }}).catch(function(){{btn.textContent='error \u2014 retry';btn.disabled=false;}});
}}
document.addEventListener('mousedown',function(e){{if(box&&!box.contains(e.target)&&!badge.contains(e.target)){{close();pend=null;}}}});
function esc(s){{return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}}
function fmt(iso){{return new Date(iso).toLocaleDateString('en-GB',{{day:'numeric',month:'short',year:'numeric'}});}}
}})();
</script>"""


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def landing():
    return LANDING


class SessionIn(BaseModel):
    root_url: str

@app.post("/session")
def create_session(body: SessionIn):
    sid = secrets.token_urlsafe(8)
    with contextlib.closing(get_db()) as con:
        con.execute("INSERT INTO sessions (id,root_url,created) VALUES (?,?,?)",
                    (sid, body.root_url.rstrip("/"), datetime.now(timezone.utc).isoformat()))
        con.commit()
    return {"id": sid}


class AnnotationIn(BaseModel):
    session_id: str
    url: str
    quote: str
    comment: str
    prefix: str = ""
    suffix: str = ""
    author: str = "anonymous"

@app.post("/annotate", status_code=201)
def create_annotation(body: AnnotationIn):
    if not body.quote.strip() or not body.comment.strip():
        raise HTTPException(400, "quote and comment required")
    with contextlib.closing(get_db()) as con:
        row = con.execute("SELECT root_url FROM sessions WHERE id=?", (body.session_id,)).fetchone()
        if not row:
            raise HTTPException(404, "session not found")
        if not body.url.startswith(row["root_url"]):
            raise HTTPException(403, "url not under session root")
        cur = con.execute(
            "INSERT INTO annotations (session_id,url,quote,comment,prefix,suffix,author,created) VALUES (?,?,?,?,?,?,?,?)",
            (body.session_id, body.url, body.quote.strip(), body.comment.strip(),
             body.prefix, body.suffix, body.author or "anonymous",
             datetime.now(timezone.utc).isoformat()))
        con.commit()
        return {"id": cur.lastrowid}


@app.get("/annotations")
def get_annotations(session: str, url: str):
    with contextlib.closing(get_db()) as con:
        rows = con.execute(
            "SELECT id,quote,comment,author,created FROM annotations WHERE session_id=? AND url=? ORDER BY created",
            (session, url)).fetchall()
    return [dict(r) for r in rows]


# ── Proxy ─────────────────────────────────────────────────────────────────────

@app.options("/r/{session_id}")
@app.options("/r/{session_id}/{path:path}")
async def proxy_options(session_id: str = "", path: str = ""):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "*"
    }
    return Response(status_code=200, headers=headers)

@app.get("/r/{session_id}")
@app.get("/r/{session_id}/{path:path}")
async def proxy(request: Request, session_id: str, path: str = ""):
    with contextlib.closing(get_db()) as con:
        row = con.execute("SELECT root_url FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Session not found")

    root_url = row["root_url"]
    parsed   = urlparse(root_url)

    # Build target URL
    if path:
        qs = ("?" + str(request.url).split("?",1)[1]) if "?" in str(request.url) else ""
        target = f"{parsed.scheme}://{parsed.netloc}/{path}{qs}"
    else:
        target = root_url

    async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
        try:
            resp = await client.get(target, headers={
                "User-Agent": "Mozilla/5.0 (compatible; Markd/1.0)",
                "Accept": "text/html,application/xhtml+xml,*/*",
            })
        except Exception as e:
            raise HTTPException(502, f"Could not reach target: {e}")

    ct = resp.headers.get("content-type", "")
    if "text/html" not in ct:
        headers = dict(resp.headers)
        headers["Access-Control-Allow-Origin"] = "*"
        headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "*"
        # Remove any problematic headers that might cause blocking
        headers.pop("content-security-policy", None)
        headers.pop("x-content-type-options", None)
        return Response(content=resp.content, status_code=resp.status_code, media_type=ct, headers=headers)

    soup = BeautifulSoup(resp.content, "html.parser")

    # Rewrite same-host links through proxy
    proxy_root = f"{BASE}/r/{session_id}"
    for tag, attr in [("a","href"),("link","href"),("script","src"),
                      ("img","src"),("form","action"),("source","src")]:
        for el in soup.find_all(tag, **{attr: True}):
            val = el[attr]
            if not val or val.startswith(("data:","javascript:","mailto:","#","tel:")):
                continue
            abs_url = urljoin(target, val)
            up = urlparse(abs_url)
            if up.netloc == parsed.netloc:
                sub = up.path.lstrip("/")
                qs2 = ("?" + up.query) if up.query else ""
                el[attr] = f"{proxy_root}/{sub}{qs2}"

    # Remove CSP meta tags so our injected script can run
    for meta in soup.find_all("meta", attrs={"http-equiv": re.compile("content-security-policy", re.I)}):
        meta.decompose()

    # Inject overlay
    frag = BeautifulSoup(overlay_script(session_id, BASE, target), "html.parser")
    (soup.body or soup).append(frag)

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "*"
    }
    # Remove problematic headers from original response
    for header in ["content-security-policy", "x-content-type-options", "x-frame-options"]:
        headers.pop(header, None)

    return HTMLResponse(content=str(soup), headers=headers)