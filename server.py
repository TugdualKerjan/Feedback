"""
Markd — feedback annotation server (simplified)
"""
import os, re, secrets, contextlib
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────────────
DB = os.environ.get("DB_PATH", "markd.db")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
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
                text_start  INTEGER DEFAULT -1,
                text_end    INTEGER DEFAULT -1,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
        """)
        con.commit()

init_db()

# ── Overlay script ────────────────────────────────────────────────────────────
def overlay_script(session_id: str, server_base: str, target_url: str) -> str:
    return f"""<script src="https://jonudell.info/hlib/standalone-anchoring.js"></script>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
#markd-badge{{position:fixed;bottom:24px;right:24px;z-index:2147483640;background:#111010;color:#f5f2eb;font-family:'Instrument Serif',Georgia,serif;font-style:italic;font-size:15px;padding:10px 18px;border-radius:100px;cursor:default;box-shadow:0 4px 20px rgba(0,0,0,.3);display:flex;align-items:center;gap:8px;user-select:none;animation:markd-in .4s cubic-bezier(.34,1.56,.64,1) both;}}
@keyframes markd-in{{from{{transform:translateY(20px);opacity:0}}to{{transform:translateY(0);opacity:1}}}}
#markd-dot{{width:8px;height:8px;background:#e8a020;border-radius:50%;animation:markd-pulse 2s ease-in-out infinite;}}
@keyframes markd-pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.5;transform:scale(.7)}}}}
.markd-mark{{background:#fde68a;border-bottom:2px solid #e8a020;cursor:pointer;transition:background .12s;}}
.markd-mark:hover{{background:#fcd34d;}}
.markd-pop{{position:fixed;z-index:2147483638;background:#111010;color:#f5f2eb;border-radius:10px;padding:13px 15px;font-family:'Instrument Serif',Georgia,serif;font-size:14px;line-height:1.5;max-width:300px;min-width:180px;box-shadow:0 8px 32px rgba(0,0,0,.4);pointer-events:none;opacity:0;transform:translateY(8px);transition:opacity .15s,transform .15s;}}
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
var cache=[],box=null,pend=null,justSelected=false;
var badge=document.createElement('div');
badge.id='markd-badge';
badge.innerHTML='<div id="markd-dot"></div>leave feedback';
document.body.appendChild(badge);
fetch(B+'/annotations?session='+S+'&url='+encodeURIComponent(PAGE)).then(r=>r.json()).then(d=>{{cache=d;console.log('Loaded annotations:',d);d.forEach(render);setupNavigationListeners();}}).catch(()=>{{}});
function render(a){{
  try{{
    console.log('Rendering annotation:',a);

    // Try position anchor first as it's most precise
    var range=null;
    if(a.start>=0&&a.end>a.start){{
      try{{
        range=anchoring.TextPositionAnchor.toRange(document.body,{{start:a.start,end:a.end}});
        console.log('Position anchor result:',range);
      }}catch(e){{
        console.log('Position anchor failed:',e);
      }}
    }}

    // If position fails, try quote anchor
    if(!range||range.toString()!==a.quote){{
      try{{
        var quoteSelector={{
          exact:a.quote,
          prefix:a.prefix||'',
          suffix:a.suffix||''
        }};
        range=anchoring.TextQuoteAnchor.toRange(document.body,quoteSelector);
        console.log('Quote anchor result:',range);
      }}catch(e){{
        console.log('Quote anchor failed:',e);
      }}
    }}

    if(range&&range.startContainer&&range.endContainer){{
      console.log('Valid range found, wrapping...');
      console.log('WrapRangeText function:',anchoring.WrapRangeText);

      // Try correct parameter order: wrapperEl first, then range
      try{{
        var mark=document.createElement('mark');
        mark.className='markd-mark';
        var result=anchoring.WrapRangeText(mark,range);
        console.log('Wrapped elements:',result);
        var elements=result.nodes||[];
      }}catch(e){{
        console.log('WrapRangeText failed, trying alternative:',e);
        // Fallback to simple wrapping
        var mark=document.createElement('mark');
        mark.className='markd-mark';
        mark.dataset.id=a.id;
        try{{
          range.surroundContents(mark);
          elements=[mark];
        }}catch(e2){{
          console.log('Fallback also failed:',e2);
          elements=[];
        }}
      }}

      if(elements.length>0){{
        elements[0].dataset.id=a.id;
        tip(elements[0],a,elements);
      }}
    }}else{{
      console.log('Could not anchor quote:',a.quote,'range:',range);
    }}
  }}catch(e){{
    console.log('Failed to render annotation:',a.quote,e);
  }}
}}
function tip(m,a,allElements){{
  console.log('Creating tip for annotation:',a,'elements:',allElements);
  var p=document.createElement('div');
  p.className='markd-pop';
  p.innerHTML='<div class="markd-pop-comment">\u201c'+esc(a.comment)+'\u201d</div><div class="markd-pop-meta">\u2014 '+esc(a.author)+' \u00b7 '+fmt(a.created)+'</div>';
  document.body.appendChild(p);

  var elements=allElements||[m];
  elements.forEach(function(el){{
    el.addEventListener('mouseenter',function(){{
      console.log('Mouse enter on element:',el,'annotation:',a);
      var rect=el.getBoundingClientRect();
      p.style.left=Math.max(8,rect.left)+'px';
      p.style.top=(rect.bottom+8)+'px';
      p.classList.add('on');
    }});
    el.addEventListener('mouseleave',function(e){{
      if(!p.contains(e.relatedTarget)){{
        p.classList.remove('on');
      }}
    }});
  }});
  p.addEventListener('mouseleave',function(){{
    p.classList.remove('on');
  }});
}}
document.addEventListener('mouseup',function(e){{
  if(box&&box.contains(e.target))return;
  if(badge.contains(e.target))return;
  var sel=getSelection(),q=sel&&sel.toString().trim();
  if(!q||q.length<3){{close();pend=null;return;}}
  var rng=sel.getRangeAt(0),rect=rng.getBoundingClientRect();
  var cn=rng.startContainer,full=cn.textContent||'';

  // Use the correct API methods
  var quoteSelector=anchoring.TextQuoteAnchor.fromRange(document.body,rng);
  var posSelector=anchoring.TextPositionAnchor.fromRange(document.body,rng);

  pend={{
    quote:q,
    prefix:quoteSelector.prefix||'',
    suffix:quoteSelector.suffix||'',
    start:posSelector.start||0,
    end:posSelector.end||0
  }};
  show(rect);
  justSelected=true; // Mark that we just processed a selection
  setTimeout(function(){{justSelected=false;}},200); // Reset after short delay
  // Don't clear selection immediately - let user see what they selected
}});
function show(rect){{
  if(!pend)return;
  var pendQuote=pend.quote;

  // Preserve selection while showing popup
  var sel=getSelection();
  var savedRange=sel.rangeCount>0?sel.getRangeAt(0).cloneRange():null;

  close();box=document.createElement('div');box.className='markd-box';
  var top=rect.bottom+10,left=Math.max(8,Math.min(rect.left,innerWidth-336));
  box.style.top=top+'px';box.style.left=left+'px';
  box.innerHTML='<div class="markd-qprev">'+esc(pendQuote)+'</div><textarea placeholder="Your thought\u2026"></textarea><input type="text" placeholder="Your name (optional)"><div class="markd-btns"><button class="markd-btn markd-cancel">cancel</button><button class="markd-btn markd-submit">annotate \u2192</button></div>';
  document.body.appendChild(box);

  // Restore selection after DOM manipulation
  if(savedRange){{
    sel.removeAllRanges();
    sel.addRange(savedRange);

    // Create yellow highlight overlay for the selected text
    var selectionRect=savedRange.getBoundingClientRect();
    var highlight=document.createElement('div');
    highlight.className='markd-selection-highlight';
    highlight.style.position='fixed';
    highlight.style.left=selectionRect.left+'px';
    highlight.style.top=selectionRect.top+'px';
    highlight.style.width=selectionRect.width+'px';
    highlight.style.height=selectionRect.height+'px';
    highlight.style.background='rgba(253, 230, 138, 0.3)';
    highlight.style.borderBottom='2px solid #e8a020';
    highlight.style.pointerEvents='none';
    highlight.style.zIndex='1';
    document.body.appendChild(highlight);
  }}

  box.querySelector('textarea').focus();
  box.querySelector('.markd-cancel').addEventListener('click',close);
  box.querySelector('.markd-submit').addEventListener('click',submit);
  box.querySelector('textarea').addEventListener('keydown',function(e){{
    if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();submit();}}
    else if(e.key==='Enter'&&(e.metaKey||e.ctrlKey))submit();
  }});
  box.querySelector('input').addEventListener('keydown',function(e){{if(e.key==='Enter')submit();}});
}}
function close(){{
  if(box){{box.remove();box=null;}}
  // Remove selection highlight
  document.querySelectorAll('.markd-selection-highlight').forEach(function(h){{h.remove();}});
}}
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
    render(a);
  }}).catch(function(){{btn.textContent='error \u2014 retry';btn.disabled=false;}});
}}
// Prevent clicks when we just made a selection
document.addEventListener('click',function(e){{
  if(justSelected||getSelection().toString().trim().length>0){{
    console.log('Preventing click on',e.target,'due to recent selection');
    e.preventDefault();
    e.stopImmediatePropagation();
    return false;
  }}
}},true);

document.addEventListener('mousedown',function(e){{
  if(box&&!box.contains(e.target)&&!badge.contains(e.target)){{close();pend=null;}}
  justSelected=false; // Reset on new mouse interaction
}});


function setupNavigationListeners(){{
  // Listen for SPA navigation events
  window.addEventListener('popstate',reanchorAll);
  window.addEventListener('hashchange',reanchorAll);

  // Patch history.pushState for SPAs that don't fire popstate
  var origPush=history.pushState.bind(history);
  var origReplace=history.replaceState.bind(history);

  history.pushState=function(){{
    origPush.apply(this,arguments);
    setTimeout(reanchorAll,100); // Small delay for DOM updates
  }};

  history.replaceState=function(){{
    origReplace.apply(this,arguments);
    setTimeout(reanchorAll,100);
  }};
}}

function reanchorAll(){{
  console.log('Navigation detected, re-anchoring annotations');
  // Clean slate
  document.querySelectorAll('.markd-mark').forEach(function(el){{el.replaceWith(...el.childNodes);}});
  document.querySelectorAll('.markd-pop').forEach(function(el){{el.remove();}});

  // Re-render
  cache.forEach(render);
}}

function esc(s){{return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}}
function fmt(iso){{return new Date(iso).toLocaleDateString('en-GB',{{day:'numeric',month:'short',year:'numeric'}});}}
}})();
</script>"""

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
    start: int = -1
    end: int = -1

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
            "INSERT INTO annotations (session_id,url,quote,comment,prefix,suffix,author,created,text_start,text_end) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (body.session_id, body.url, body.quote.strip(), body.comment.strip(),
             body.prefix, body.suffix, body.author or "anonymous",
             datetime.now(timezone.utc).isoformat(), body.start, body.end))
        con.commit()
        return {"id": cur.lastrowid}

@app.get("/annotations")
def get_annotations(session: str, url: str):
    with contextlib.closing(get_db()) as con:
        rows = con.execute(
            "SELECT id,quote,comment,author,created,prefix,suffix,text_start as start,text_end as end FROM annotations WHERE session_id=? AND url=? ORDER BY created",
            (session, url)).fetchall()
    return [dict(r) for r in rows]

# ── Proxy ─────────────────────────────────────────────────────────────────────

@app.options("/r/{session_id}")
@app.options("/r/{session_id}/{path:path}")
async def proxy_options():
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
    parsed = urlparse(root_url)

    # Build target URL
    if path:
        qs = ("?" + str(request.url).split("?",1)[1]) if "?" in str(request.url) else ""
        # For absolute paths (starting with /), use the domain root
        if path.startswith('/'):
            target = f"{parsed.scheme}://{parsed.netloc}{path}{qs}"
        else:
            # For relative paths, join with root_url
            base_url = root_url if root_url.endswith('/') else root_url + '/'
            target = urljoin(base_url, path) + qs
        print(f"DEBUG: path={path}, root_url={root_url}, target={target}")
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

    # Handle non-HTML resources
    if "text/html" not in ct:
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, f"Resource not found: {target}")

        headers = dict(resp.headers)
        headers["Access-Control-Allow-Origin"] = "*"
        headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "*"
        headers.pop("content-security-policy", None)
        headers.pop("x-content-type-options", None)
        headers.pop("content-length", None)  # Remove content-length to avoid mismatch
        headers.pop("content-encoding", None)  # Remove content-encoding since we're serving uncompressed
        return Response(content=resp.content, status_code=resp.status_code, media_type=ct, headers=headers)

    soup = BeautifulSoup(resp.content, "html.parser")

    # Rewrite same-host links through proxy
    proxy_root = f"{BASE_URL}/r/{session_id}"
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

    # Remove CSP meta tags
    for meta in soup.find_all("meta", attrs={"http-equiv": re.compile("content-security-policy", re.I)}):
        meta.decompose()

    # Remove CSP-blocking base tags
    for base in soup.find_all("base"):
        base.decompose()

    # Inject fetch interception script to handle relative URLs
    parsed_target = urlparse(target)
    if '.' in parsed_target.path.split('/')[-1]:  # Target is a file
        base_path = '/'.join(parsed_target.path.split('/')[:-1]) + '/'
        base_url = f"{parsed_target.scheme}://{parsed_target.netloc}{base_path}"
    else:
        base_url = target.rstrip('/') + '/'

    fetch_script = soup.new_tag("script")
    fetch_script.string = f"""
    (function() {{
        const originalFetch = window.fetch;
        const baseUrl = '{base_url}';
        window.fetch = function(url, options) {{
            if (typeof url === 'string' && (url.startsWith('./') || url.startsWith('../') || (!url.includes('://') && !url.startsWith('/')))) {{
                url = new URL(url, baseUrl).href;
            }}
            return originalFetch.call(this, url, options);
        }};
    }})();
    """
    (soup.head or soup).insert(0, fetch_script)

    # Inject overlay
    frag = BeautifulSoup(overlay_script(session_id, BASE_URL, target), "html.parser")
    (soup.body or soup).append(frag)

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "*"
    }
    # Don't copy original headers that could cause issues
    for header in ["content-security-policy", "x-content-type-options", "x-frame-options", "content-length"]:
        headers.pop(header, None)

    return HTMLResponse(content=str(soup), headers=headers)