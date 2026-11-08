"""Security audit 2: Jackett/Prowlarr download links (which carry the indexer API key,
and for private trackers the passkey) never reach the browser."""
from unittest.mock import patch

from app.models import Download

from tests.test_organise import lib  # noqa: F401
from tests.test_pipeline import env  # noqa: F401

LINK = "http://prowlarr:9696/1/download?apikey=PROWLARRKEY0123456789&link=abc"
RESULTS = [
    {"title": "Show S01E01 1080p x265", "size": "1 GB", "size_bytes": 1, "seeds": 9, "leeches": 0,
     "magnet": LINK, "info_hash": "", "quality": "1080p", "source": "prowlarr/x",
     "url": "https://tracker.example/details?id=5&passkey=PASSKEY0123456789ab"},
    {"title": "Show S01E02 1080p x265", "size": "1 GB", "size_bytes": 1, "seeds": 9, "leeches": 0,
     "magnet": "magnet:?xt=urn:btih:" + "a" * 40, "info_hash": "a" * 40, "quality": "1080p", "source": "tpb"},
]


def test_search_hides_indexer_links(lib):
    client, Session, _ = lib
    with patch("app.routers.search.search_all", return_value=[dict(r) for r in RESULTS]):
        r = client.get("/api/search?q=show")
    assert r.status_code == 200
    assert "PROWLARRKEY" not in r.text and "PASSKEY" not in r.text
    first, second = r.json()
    assert first["magnet"].startswith("mmlink:") and first["url"] == "https://tracker.example/details?id=5"
    assert second["magnet"] == RESULTS[1]["magnet"]                       # magnets carry no secrets


def test_adding_a_handle_sends_the_real_link(lib):
    client, Session, _ = lib
    with patch("app.routers.search.search_all", return_value=[dict(r) for r in RESULTS]):
        handle = client.get("/api/search?q=show").json()[0]["magnet"]
    with patch("app.routers.downloads.add_torrent", return_value="b" * 40) as add:
        r = client.post("/api/downloads", json={"magnet": handle, "title": "Show S01E01"})
    assert r.status_code == 201 and add.call_args[0][0] == LINK
    with Session() as s:
        assert s.query(Download).one().magnet_link == LINK                 # stays server-side
    assert "PROWLARRKEY" not in client.get("/api/downloads").text


def test_an_unknown_handle_is_refused(lib):
    client, _, _ = lib
    r = client.post("/api/downloads", json={"magnet": "mmlink:" + "0" * 24, "title": "x"})
    assert r.status_code == 400 and "Search again" in r.json()["detail"]
