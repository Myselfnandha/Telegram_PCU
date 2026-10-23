"""
Telegram Web Reverse Proxy for TG Power Suite.
Proxies official Telegram Web (WebK / WebA) to run inside an embedded iframe
without being blocked by X-Frame-Options: DENY or Content-Security-Policy.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
import httpx

logger = logging.getLogger("tgweb_proxy")

router = APIRouter(prefix="/api/tgweb", tags=["tgweb"])

# Persistent HTTP client with connection pooling for sub-millisecond proxying
_client: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0),
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=40)
        )
    return _client


# Headers to strip from Telegram server response to enable iframe embedding & clean delivery
STRIP_RESPONSE_HEADERS = {
    "x-frame-options",
    "content-security-policy",
    "content-security-policy-report-only",
    "content-encoding",
    "transfer-encoding",
    "connection",
    "keep-alive",
}


@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
async def redirect_root():
    return RedirectResponse(url="/api/tgweb/k/", status_code=307)


@router.get("/k", include_in_schema=False)
async def redirect_k():
    return RedirectResponse(url="/api/tgweb/k/", status_code=307)


@router.get("/a", include_in_schema=False)
async def redirect_a():
    return RedirectResponse(url="/api/tgweb/a/", status_code=307)


@router.api_route("/{flavor}/{subpath:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def proxy_tgweb(flavor: str, subpath: str, request: Request):
    """
    Reverse proxies requests to https://web.telegram.org/{flavor}/{subpath}.
    Strips X-Frame-Options and injects <base href> for HTML to ensure all assets
    route smoothly through this proxy origin.
    """
    # Validate flavor ('k' is WebK, 'a' is WebA/WebZ)
    if flavor not in ("k", "a"):
        # If someone calls /api/tgweb/assets/..., assume 'k'
        real_flavor = "k"
        real_subpath = f"{flavor}/{subpath}" if subpath else flavor
    else:
        real_flavor = flavor
        real_subpath = subpath

    target_url = f"https://web.telegram.org/{real_flavor}/{real_subpath}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    # Prepare request headers
    forward_headers = {}
    for h_name, h_val in request.headers.items():
        h_lower = h_name.lower()
        if h_lower not in ("host", "referer", "content-length"):
            forward_headers[h_name] = h_val

    # Set appropriate Referer and Origin for Telegram's upstream servers
    forward_headers["Referer"] = f"https://web.telegram.org/{real_flavor}/"
    forward_headers["Origin"] = "https://web.telegram.org"

    client = get_http_client()

    try:
        req_body = await request.body() if request.method in ("POST", "PUT", "PATCH") else None
        
        upstream_res = await client.request(
            method=request.method,
            url=target_url,
            headers=forward_headers,
            content=req_body,
        )

        # Build clean response headers
        resp_headers = {}
        for h_k, h_v in upstream_res.headers.items():
            if h_k.lower() not in STRIP_RESPONSE_HEADERS:
                resp_headers[h_k] = h_v

        # Ensure CORS is open for assets/workers
        resp_headers["Access-Control-Allow-Origin"] = "*"
        resp_headers["Access-Control-Allow-Methods"] = "GET, POST, HEAD, OPTIONS"

        content_type = upstream_res.headers.get("content-type", "").lower()

        # HTML modification: Inject <base href> and ServiceWorker registration tweaks if needed
        if "text/html" in content_type:
            html = upstream_res.text
            base_tag = f'<base href="/api/tgweb/{real_flavor}/">'
            
            if "<head>" in html:
                html = html.replace("<head>", f"<head>\n    {base_tag}", 1)
            elif "<head " in html:
                # If head tag has attributes
                idx = html.find(">")
                if idx != -1:
                    html = html[:idx+1] + f"\n    {base_tag}" + html[idx+1:]
            
            # Remove any inline frame protection scripts if present
            return Response(
                content=html,
                status_code=upstream_res.status_code,
                media_type="text/html",
                headers=resp_headers
            )

        # Non-HTML content (JS, CSS, images, fonts, WASM): return raw binary content
        return Response(
            content=upstream_res.content,
            status_code=upstream_res.status_code,
            headers=resp_headers
        )

    except httpx.RequestError as exc:
        logger.warning(f"Telegram Web proxy request error for {target_url}: {exc}")
        return Response(
            content=f"<html><body style='background:#0f172a;color:#f87171;font-family:sans-serif;padding:24px;'><h3>Telegram Web Proxy Error</h3><p>{str(exc)}</p></body></html>",
            status_code=502,
            media_type="text/html"
        )
