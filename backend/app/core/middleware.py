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
        return response
