import pathlib
import sys
from unittest.mock import MagicMock

# Ensure the backend package is importable when running pytest from any directory.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# qbittorrentapi is not installed in the test environment.  Stub it out so that
# modules that import it at module-level (e.g. app.routers.downloads) can be
# collected by pytest without errors.  Individual tests that exercise qBittorrent
# behaviour mock the relevant functions directly.
if "qbittorrentapi" not in sys.modules:
    sys.modules["qbittorrentapi"] = MagicMock()


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_tvmaze(monkeypatch):
    """Tests must never reach the real TVMaze API; an accidental call fails fast instead."""
    from app.services import tvmaze

    monkeypatch.setattr(tvmaze, "_BASE", "http://tvmaze.invalid")


@pytest.fixture(autouse=True)
def _no_real_tmdb(monkeypatch):
    """Same for TMDB."""
    from app.services import tmdb

    monkeypatch.setattr(tmdb, "_BASE", "http://tmdb.invalid")


@pytest.fixture(autouse=True)
def _no_wait_for_qbittorrent(monkeypatch):
    """Don't wait for qBittorrent to confirm a new torrent in tests."""
    from app.services import qbittorrent
    monkeypatch.setattr(qbittorrent, "CONFIRM_WAIT", 0)


@pytest.fixture(autouse=True)
def _testclient_from_home(monkeypatch):
    """TestClient reports its address as "testclient", which the login check can't
    place, so requests would need a password. Make them arrive from a home address
    unless a test sets its own (tests.helpers.from_address)."""
    from app.middleware import auth

    real = auth.from_home

    def from_home(request):
        if request.client and request.client.host == "testclient":
            return "cf-connecting-ip" not in request.headers
        return real(request)
    monkeypatch.setattr(auth, "from_home", from_home)
