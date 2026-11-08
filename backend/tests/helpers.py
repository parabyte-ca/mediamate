"""Small test helpers shared across suites."""


def from_address(app, host: str, port: int = 50000):
    """Wrap an ASGI app so every request arrives from ``host`` (TestClient reports
    "testclient", which isn't an address the login check can classify)."""
    async def wrapped(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": (host, port)}
        await app(scope, receive, send)
    return wrapped
