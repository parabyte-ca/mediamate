"""The last line of defence: no JSON response leaves the server with a secret in it.

Any stored secret value (passwords, API keys, tokens) and any credential in a URL
(``api_key=``, ``X-Plex-Token=``, ``passkey=`` …) found in an ``application/json``
response is replaced with the mask. Error text from outside services (an httpx error
naming its URL, a Plex error) can't leak a key, whatever a route returns. Streams,
files and server-sent events are passed through untouched."""
import time

from ..services.secret_settings import MASK, SECRET_KEYS, SERVER_ONLY_KEYS, scrub_urls

_KEYS = SECRET_KEYS | SERVER_ONLY_KEYS | {"tpdb_api_key"}
_TTL = 30.0
_cache: dict = {"at": 0.0, "values": ()}
MAX_BODY = 4 * 1024 * 1024


def _secrets() -> tuple[str, ...]:
    now = time.monotonic()
    if now - _cache["at"] > _TTL:
        try:
            from ..database import SessionLocal
            from ..models import Setting
            with SessionLocal() as db:
                vals = [r.value for r in db.query(Setting).filter(Setting.key.in_(_KEYS)).all()]
            _cache["values"] = tuple(sorted({v for v in vals if v and len(v) >= 8 and v != MASK},
                                            key=len, reverse=True))
        except Exception:
            pass                                    # keep the last known values
        _cache["at"] = now
    return _cache["values"]


def forget() -> None:
    """Settings changed: reload the secret values on the next response."""
    _cache["at"] = 0.0


def clean(text: str) -> str:
    for v in _secrets():
        if v in text:
            text = text.replace(v, MASK)
    return scrub_urls(text)


class ScrubMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        start = {}
        chunks = []

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                if b"application/json" not in headers.get(b"content-type", b""):
                    start["pass"] = True
                    return await send(message)
                start["msg"] = message
                return
            if start.get("pass"):
                return await send(message)
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                if sum(map(len, chunks)) > MAX_BODY:          # too big to hold: send as it is
                    start["pass"] = True
                    await send(start["msg"])
                    await send({"type": "http.response.body", "body": b"".join(chunks), "more_body": True})
                return
            body = b"".join(chunks)
            try:
                body = clean(body.decode("utf-8")).encode("utf-8")
            except UnicodeDecodeError:
                pass
            msg = start["msg"]
            headers = [(k, v) for k, v in msg.get("headers", []) if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode()))
            await send({**msg, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, wrapped)
