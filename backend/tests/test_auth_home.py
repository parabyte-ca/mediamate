"""Only the home network skips the login (security audit 2): a request through Nginx
Proxy Manager without Cloudflare, or a forwarded port, needs the password."""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.middleware.auth import AuthMiddleware
from app.models import Setting
from app.services.token import make_token

from tests.helpers import from_address


def _app(Session, host, password="hunter2"):
    with Session() as s:
        s.query(Setting).delete()
        if password:
            s.add(Setting(key="ui_password", value=password))
        s.add(Setting(key="session_secret", value="s3cret"))
        s.commit()
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/thing")
    def thing():
        return {"ok": True}
    return TestClient(from_address(app, host))


@pytest.fixture
def Session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with patch("app.middleware.auth.SessionLocal", S):
        yield S


@pytest.mark.parametrize("host,headers,code", [
    ("192.168.1.20", {}, 200),
    ("10.0.0.5", {}, 200),
    ("172.18.0.3", {"x-forwarded-for": "192.168.1.20"}, 200),       # NPM on the LAN
    ("127.0.0.1", {}, 200),
    ("8.8.8.8", {}, 401),                                         # a forwarded port
    ("172.18.0.3", {"x-forwarded-for": "8.8.8.8"}, 401),         # NPM passing an outside visitor
    ("172.18.0.3", {"x-real-ip": "1.1.1.1"}, 401),
    ("172.18.0.3", {"cf-connecting-ip": "8.8.8.8"}, 401),        # Cloudflare
])
def test_who_skips_the_login(Session, host, headers, code):
    assert _app(Session, host).get("/api/thing", headers=headers).status_code == code


def test_a_token_still_works_from_outside(Session):
    c = _app(Session, "8.8.8.8")
    tok = make_token("s3cret", "hunter2")
    assert c.get("/api/thing", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_no_password_set_lets_everyone_in(Session):
    assert _app(Session, "8.8.8.8", password="").get("/api/thing").status_code == 200
