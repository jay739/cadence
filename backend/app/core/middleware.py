"""Security headers middleware: CSP, X-Content-Type-Options, Referrer-Policy, frame-ancestors.

Applied to every response per the mission's non-negotiable list. CSP is intentionally strict:
no inline scripts/styles beyond what the built web client needs (Vite production builds emit
hashed, externally-referenced JS/CSS, so 'unsafe-inline' is not required for scripts; a narrow
allowance is made for style-src to accommodate CSS-in-JS/utility frameworks that inject a
<style> tag, a common and low-risk pattern - inline *scripts* are never allowed).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def _cache_control_for(path: str) -> str:
    """Cache-Control value for a response, based on request path.

    - `/api/*`: never cached. Every response here is either per-user (auth, requests) or must
      reflect live server state on the next call (search, health, settings) with no client-side
      staleness window.
    - `/assets/*`: Vite's production build names every JS/CSS file `<name>-<contenthash>.<ext>`,
      so a given URL's bytes never change; a 1-year immutable cache is safe and skips the
      revalidation round trip entirely on every repeat visit.
    - Everything else (`index.html`, favicons, PWA icons): these keep the SAME filename across
      a redeploy but their content can change, so they're revalidated rather than cached
      immutably; the browser still avoids a full re-download when nothing changed (a 304).
    """
    if path.startswith("/api/"):
        return "no-store"
    if path.startswith("/assets/"):
        return "public, max-age=31536000, immutable"
    return "no-cache"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )
        response.headers["Cache-Control"] = _cache_control_for(request.url.path)
        return response
