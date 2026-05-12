import contextlib
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from config import BASE_URL, TIMEOUT
from db import get_db
from overlay import overlay_script

router = APIRouter()

CSS_URL_PATTERN = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.I)
CSS_IMPORT_PATTERN = re.compile(r"@import\s+(url\()?(['\"]?)([^'\";\)]+)\2\)?", re.I)


def _proxied_root_relative_url(raw_url: str, session_id: str, root_parsed):
    if not raw_url or not raw_url.startswith("/") or raw_url.startswith("//") or raw_url.startswith(f"/r/"):
        return raw_url

    abs_url = f"{root_parsed.scheme}://{root_parsed.netloc}{raw_url}"
    parsed = urlparse(abs_url)
    path = parsed.path.lstrip("/")
    proxied = f"/r/{session_id}/{path}"
    if parsed.query:
        proxied += f"?{parsed.query}"
    if parsed.fragment:
        proxied += f"#{parsed.fragment}"
    return proxied


def _rewrite_css_match(match, url_group, session_id, root_parsed):
    raw_url = match.group(url_group)
    proxied = _proxied_root_relative_url(raw_url, session_id, root_parsed)
    if proxied == raw_url:
        return match.group(0)

    start, end = match.span(url_group)
    return match.string[match.start():start] + proxied + match.string[end:match.end()]


def rewrite_css_references(css_text: str, session_id: str, root_parsed):
    result = CSS_URL_PATTERN.sub(lambda m: _rewrite_css_match(m, 2, session_id, root_parsed), css_text)
    result = CSS_IMPORT_PATTERN.sub(lambda m: _rewrite_css_match(m, 3, session_id, root_parsed), result)
    return result


def rewrite_srcset_value(value: str, session_id: str, root_parsed):
    entries = []
    changed = False
    for chunk in value.split(','):
        candidate = chunk.strip()
        if not candidate:
            continue
        parts = candidate.split()
        url_token = parts[0]
        descriptor = ' '.join(parts[1:]) if len(parts) > 1 else ''
        rewritten = _proxied_root_relative_url(url_token, session_id, root_parsed)
        if rewritten != url_token:
            changed = True
        entry = rewritten
        if descriptor:
            entry += ' ' + descriptor
        entries.append(entry)
    return value if not changed else ', '.join(entries)


@router.options("/r/{session_id}")
@router.options("/r/{session_id}/{path:path}")
async def proxy_options():
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "*"
    }
    return Response(status_code=200, headers=headers)


@router.get("/r/{session_id}")
@router.get("/r/{session_id}/{path:path}")
async def proxy(request: Request, session_id: str, path: str = ""):
    with contextlib.closing(get_db()) as con:
        row = con.execute("SELECT root_url FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Session not found")

    root_url = row["root_url"]
    root_parsed = urlparse(root_url)

    if path:
        qs = ("?" + str(request.url).split("?", 1)[1]) if "?" in str(request.url) else ""
        if path.startswith('/'):
            target = f"{root_parsed.scheme}://{root_parsed.netloc}{path}{qs}"
        else:
            base_url = root_url if root_url.endswith('/') else root_url + '/'
            target = urljoin(base_url, path) + qs
    else:
        target = root_url

    async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
        try:
            resp = await client.get(target, headers={
                "User-Agent": "Mozilla/5.0 (compatible; Marc/1.0)",
                "Accept": "text/html,application/xhtml+xml,*/*",
            })
        except Exception as exc:
            raise HTTPException(502, f"Could not reach target: {exc}")

    ct = resp.headers.get("content-type", "")

    if "text/html" not in ct:
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, f"Resource not found: {target}")

        content = resp.content
        if "text/css" in ct:
            css = rewrite_css_references(resp.text, session_id, root_parsed)
            encoding = resp.encoding or "utf-8"
            content = css.encode(encoding, errors="replace")

        headers = dict(resp.headers)
        headers["Access-Control-Allow-Origin"] = "*"
        headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "*"
        for header in ["content-security-policy", "x-content-type-options", "cross-origin-resource-policy",
                       "cross-origin-opener-policy", "cross-origin-embedder-policy", "content-length", "content-encoding"]:
            headers.pop(header, None)
        return Response(content=content, status_code=resp.status_code, media_type=ct, headers=headers)

    soup = BeautifulSoup(resp.content, "html.parser")

    for meta in soup.find_all("meta", attrs={"http-equiv": re.compile("content-security-policy", re.I)}):
        meta.decompose()

    for base in soup.find_all("base"):
        base.decompose()

    for el in soup.find_all(["img", "source"]):
        srcset = el.get("srcset")
        if srcset:
            rewritten = rewrite_srcset_value(srcset, session_id, root_parsed)
            if rewritten != srcset:
                el["srcset"] = rewritten

    base_href = f"{BASE_URL.rstrip('/')}/r/{session_id}/"
    base_tag = soup.new_tag("base", href=base_href)
    if soup.head:
        soup.head.insert(0, base_tag)
    else:
        soup.insert(0, base_tag)

    frag = BeautifulSoup(overlay_script(session_id, BASE_URL, target), "html.parser")
    (soup.body or soup).append(frag)

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "*"
    }
    for header in ["content-security-policy", "x-content-type-options", "x-frame-options", "content-length",
                   "cross-origin-resource-policy", "cross-origin-opener-policy", "cross-origin-embedder-policy"]:
        headers.pop(header, None)

    return HTMLResponse(content=str(soup), headers=headers)
