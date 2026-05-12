import contextlib
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from config import TIMEOUT
from db import get_db
from overlay import overlay_script

router = APIRouter()

CSS_URL_PATTERN = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.I)
CSS_IMPORT_PATTERN = re.compile(r"@import\s+(url\()?(['\"]?)([^'\";\)]+)\2\)?", re.I)
META_REFRESH_PATTERN = re.compile(r"(^|;\s*)url=(.+)$", re.I)
HTML_URL_ATTRS = ("href", "src", "action", "formaction", "poster", "data")
SKIPPED_URL_SCHEMES = {"data", "javascript", "mailto", "tel", "blob", "about"}


def _proxied_url(raw_url: str, session_id: str, root_parsed, base_url: str):
    if not raw_url:
        return raw_url

    if raw_url.startswith("#") or raw_url.startswith(f"/r/"):
        return raw_url

    parsed = urlparse(raw_url)
    if parsed.scheme in SKIPPED_URL_SCHEMES:
        return raw_url

    if parsed.scheme and parsed.netloc:
        abs_url = raw_url
    else:
        abs_url = urljoin(base_url, raw_url)
    parsed = urlparse(abs_url)

    if parsed.scheme != root_parsed.scheme or parsed.netloc != root_parsed.netloc:
        return raw_url

    path = parsed.path.lstrip("/")
    proxied = f"/r/{session_id}"
    if path:
        proxied += f"/{path}"
    if parsed.query:
        proxied += f"?{parsed.query}"
    if parsed.fragment:
        proxied += f"#{parsed.fragment}"
    return proxied


def _rewrite_css_match(match, url_group, session_id, root_parsed, base_url):
    raw_url = match.group(url_group)
    proxied = _proxied_url(raw_url, session_id, root_parsed, base_url)
    if proxied == raw_url:
        return match.group(0)

    start, end = match.span(url_group)
    return match.string[match.start():start] + proxied + match.string[end:match.end()]


def rewrite_css_references(css_text: str, session_id: str, root_parsed, base_url: str):
    result = CSS_URL_PATTERN.sub(lambda m: _rewrite_css_match(m, 2, session_id, root_parsed, base_url), css_text)
    result = CSS_IMPORT_PATTERN.sub(lambda m: _rewrite_css_match(m, 3, session_id, root_parsed, base_url), result)
    return result


def rewrite_srcset_value(value: str, session_id: str, root_parsed, base_url: str):
    entries = []
    changed = False
    for chunk in value.split(','):
        candidate = chunk.strip()
        if not candidate:
            continue
        parts = candidate.split()
        url_token = parts[0]
        descriptor = ' '.join(parts[1:]) if len(parts) > 1 else ''
        rewritten = _proxied_url(url_token, session_id, root_parsed, base_url)
        if rewritten != url_token:
            changed = True
        entry = rewritten
        if descriptor:
            entry += ' ' + descriptor
        entries.append(entry)
    return value if not changed else ', '.join(entries)


def rewrite_html_url_attributes(soup: BeautifulSoup, session_id: str, root_parsed, base_url: str):
    for el in soup.find_all(True):
        for attr in HTML_URL_ATTRS:
            raw_url = el.get(attr)
            if not raw_url:
                continue
            rewritten = _proxied_url(raw_url, session_id, root_parsed, base_url)
            if rewritten == raw_url:
                continue
            el[attr] = rewritten
            # Proxied assets may have their bytes rewritten, so SRI can no longer be trusted.
            el.attrs.pop("integrity", None)
            el.attrs.pop("crossorigin", None)

        inline_style = el.get("style")
        if inline_style:
            rewritten_style = rewrite_css_references(inline_style, session_id, root_parsed, base_url)
            if rewritten_style != inline_style:
                el["style"] = rewritten_style


def rewrite_meta_refresh(soup: BeautifulSoup, session_id: str, root_parsed, base_url: str):
    for meta in soup.find_all("meta", attrs={"http-equiv": re.compile("refresh", re.I)}):
        content = meta.get("content")
        if not content:
            continue
        match = META_REFRESH_PATTERN.search(content)
        if not match:
            continue
        rewritten = _proxied_url(match.group(2).strip(), session_id, root_parsed, base_url)
        if rewritten == match.group(2).strip():
            continue
        meta["content"] = content[:match.start(2)] + rewritten + content[match.end(2):]


def proxied_base_href(base_url: str, session_id: str, server_base: str) -> str:
    parsed = urlparse(base_url)
    path = parsed.path or "/"
    if path.endswith("/"):
        directory = path
    elif "/" in path:
        directory = path.rsplit("/", 1)[0] + "/"
    else:
        directory = "/"
    proxied = f"{server_base.rstrip('/')}/r/{session_id}"
    if directory != "/":
        proxied += directory
    else:
        proxied += "/"
    return proxied


def request_origin(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}"
    return str(request.base_url).rstrip("/")


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
    server_base = request_origin(request)

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
            css = rewrite_css_references(resp.text, session_id, root_parsed, target)
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
            rewritten = rewrite_srcset_value(srcset, session_id, root_parsed, target)
            if rewritten != srcset:
                el["srcset"] = rewritten

    rewrite_html_url_attributes(soup, session_id, root_parsed, target)
    rewrite_meta_refresh(soup, session_id, root_parsed, target)

    base_href = proxied_base_href(target, session_id, server_base)
    base_tag = soup.new_tag("base", href=base_href)
    if soup.head:
        soup.head.insert(0, base_tag)
    else:
        soup.insert(0, base_tag)

    frag = BeautifulSoup(overlay_script(session_id, server_base, target), "html.parser")
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
