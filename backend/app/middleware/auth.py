import hmac
import ipaddress

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..database import SessionLocal
from ..models import Setting
from ..services.token import make_token

_EXEMPT = {"/health", "/api/auth/login", "/api/auth/status"}


def _private(addr: str | None) -> bool:
    try:
        ip = ipaddress.ip_address((addr or "").strip().strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def from_home(request: Request) -> bool:
    """A request from the home network: no Cloudflare header, the client address is
    private (192.168.x, 10.x, 172.16–31.x, localhost), and so is every address a proxy
    in between reports (X-Forwarded-For / X-Real-IP)."""
    h = request.headers
    if "cf-connecting-ip" in h:
        return False
    if not _private(request.client.host if request.client else None):
        return False
    forwarded = [a for a in (h.get("x-forwarded-for") or "").split(",") if a.strip()]
    if h.get("x-real-ip"):
        forwarded.append(h["x-real-ip"])
    return all(_private(a) for a in forwarded)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # OPTIONS (CORS pre-flights) and non-API paths (static files, SPA) always pass through
        if request.method == "OPTIONS" or not request.url.path.startswith("/api/"):
            return await call_next(request)

        # Specific exempt API paths (login, status), and share links (their token is the key)
        if request.url.path in _EXEMPT or request.url.path.startswith("/api/public/"):
            return await call_next(request)

        # From the home network → always allowed
        if from_home(request):
            return await call_next(request)

        # Anything else (Cloudflare, a public address, a proxy forwarding one): load password + secret
        db = SessionLocal()
        try:
            rows = {
                r.key: r.value
                for r in db.query(Setting).filter(
                    Setting.key.in_(["ui_password", "session_secret"])
                ).all()
            }
        finally:
            db.close()

        password = rows.get("ui_password", "")
        if not password:
            # Password not configured → CF requests also pass through
            return await call_next(request)

        # Require valid Bearer token. GET requests may pass it as ?token= instead,
        # because <video src> and EventSource cannot send an Authorization header.
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            candidate = auth_header[len("Bearer "):].strip()
        elif request.method == "GET" and request.query_params.get("token"):
            candidate = request.query_params["token"]
        else:
            return JSONResponse({"detail": "Authentication required."}, status_code=401)

        secret = rows.get("session_secret", "")
        if not secret:
            return JSONResponse({"detail": "Auth not initialised. Please log in."}, status_code=401)

        expected = make_token(secret, password)
        if not hmac.compare_digest(expected, candidate):
            return JSONResponse({"detail": "Invalid or expired token."}, status_code=401)

        return await call_next(request)
