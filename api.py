import contextlib
import ipaddress
import secrets
import socket
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config import SESSION_RATE_LIMIT, SESSION_RATE_WINDOW
from db import get_db
from overlay import LANDING

router = APIRouter()
_session_rate_store = defaultdict(deque)


def _is_disallowed_host(hostname: str) -> bool:
    infos = socket.getaddrinfo(hostname, None)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def _enforce_session_rate_limit(ip: str):
    now = time.time()
    bucket = _session_rate_store[ip]
    while bucket and bucket[0] <= now - SESSION_RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= SESSION_RATE_LIMIT:
        raise HTTPException(429, "Too many session creation requests")
    bucket.append(now)


@router.get("/", response_class=HTMLResponse)
def landing():
    return LANDING


class SessionIn(BaseModel):
    root_url: str


@router.post("/session")
def create_session(body: SessionIn, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    _enforce_session_rate_limit(client_ip)

    parsed_root = urlparse(body.root_url)
    if parsed_root.scheme not in ("http", "https") or not parsed_root.netloc:
        raise HTTPException(400, "root_url must be an http(s) URL")

    hostname = parsed_root.hostname
    if not hostname:
        raise HTTPException(400, "root_url missing hostname")

    try:
        if _is_disallowed_host(hostname):
            raise HTTPException(403, "root_url resolves to a disallowed address")
    except OSError as exc:
        raise HTTPException(400, f"Could not resolve root_url: {exc}")

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


@router.post("/annotate", status_code=201)
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


@router.get("/annotations")
def get_annotations(session: str, url: str = None):
    with contextlib.closing(get_db()) as con:
        if url:
            rows = con.execute(
                "SELECT id,quote,comment,author,created,prefix,suffix,text_start as start,text_end as end,url FROM annotations WHERE session_id=? AND url=? ORDER BY created",
                (session, url)).fetchall()
        else:
            rows = con.execute(
                "SELECT id,quote,comment,author,created,prefix,suffix,text_start as start,text_end as end,url FROM annotations WHERE session_id=? ORDER BY created DESC",
                (session,)).fetchall()
    return [dict(r) for r in rows]
