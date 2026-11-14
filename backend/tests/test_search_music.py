"""Search → Music: artists and albums from MusicBrainz plus your own library, each
flagged with what you already have (following, wanted, in your library)."""
import pytest
from fastapi.testclient import TestClient

from app.models import MusicAlbum, MusicLibAlbum, WatchlistItem
from app.routers import browse as browse_router
from app.services import musicbrainz

from tests.test_watchlist_movies import Session, db  # noqa: F401

ARTISTS = {"artists": [
    {"id": "a-sd", "name": "Slowdive", "country": "GB", "type": "Group", "score": 100,
     "life-span": {"begin": "1989"}},
    {"id": "a-other", "name": "Slowdive Tribute", "score": 60},
]}
GROUPS = {"release-groups": [
    {"id": "rg-souv", "title": "Souvlaki", "first-release-date": "1993-05-17", "primary-type": "Album",
     "artist-credit": [{"name": "Slowdive", "artist": {"id": "a-sd"}}]},
    {"id": "rg-ev", "title": "everything is alive", "first-release-date": "2023-09-01", "primary-type": "Album",
     "artist-credit": [{"name": "Slowdive", "artist": {"id": "a-sd"}}]},
    {"id": "rg-noone", "title": "Orphan", "artist-credit": []},
]}


@pytest.fixture
def mb(monkeypatch):
    browse_router._music_search_cache.clear()
    calls = []

    def fake_get(path, **params):
        calls.append(path)
        return ARTISTS if path == "/artist" else GROUPS
    monkeypatch.setattr(musicbrainz, "_get", fake_get)
    return calls


@pytest.fixture
def client(Session):  # noqa: F811
    from app.database import get_db
    from app.main import app

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_release_groups_carry_artist_ids(mb):
    rows = musicbrainz.search_release_groups(None, None, text="slowdive")
    assert rows[0]["artist_mbids"] == ["a-sd"] and rows[2]["artist_mbids"] == []


def test_sections_and_flags(mb, client, db):  # noqa: F811
    w = WatchlistItem(title="Slowdive", search_query="Slowdive", kind="artist", mbid="a-sd")
    db.add(w)
    db.commit()
    db.add(MusicAlbum(watchlist_id=w.id, mbid="rg-ev", title="everything is alive", status="wanted"))
    db.add(MusicLibAlbum(folder="/m/sd/souv", artist="Slowdive", title="Souvlaki", year=1993, format="flac",
                         cover="cover.jpg"))
    db.add(MusicLibAlbum(folder="/m/x", artist="Someone Else", title="Nothing", format="mp3"))
    db.commit()

    r = client.get("/api/search/music?q=slowdive").json()
    assert r["mb_error"] is None
    assert [a["title"] for a in r["library"]] == ["Souvlaki"] and r["library"][0]["has_cover"]
    sd = r["artists"][0]
    assert sd["name"] == "Slowdive" and sd["following"] and sd["watchlist_id"] == w.id
    assert not r["artists"][1]["following"]
    by = {a["title"]: a for a in r["albums"]}
    assert "Orphan" not in by                                  # no artist: can't be added
    assert by["Souvlaki"]["in_library"] and by["Souvlaki"]["following"]
    assert by["everything is alive"]["status"] == "wanted"


def test_short_query_is_refused(mb, client):
    assert client.get("/api/search/music?q=a").status_code == 400


def test_musicbrainz_down_still_lists_your_library(monkeypatch, client, db):  # noqa: F811
    browse_router._music_search_cache.clear()

    def down(path, **params):
        raise musicbrainz.MusicBrainzUnavailable("timed out")
    monkeypatch.setattr(musicbrainz, "_get", down)
    db.add(MusicLibAlbum(folder="/m/sd/souv", artist="Slowdive", title="Souvlaki", format="flac"))
    db.commit()
    r = client.get("/api/search/music?q=slow").json()
    assert [a["title"] for a in r["library"]] == ["Souvlaki"]
    assert r["artists"] == [] and "unavailable" in r["mb_error"]


def test_repeat_searches_use_the_cache(mb, client):
    client.get("/api/search/music?q=slowdive")
    n = len(mb)
    client.get("/api/search/music?q=Slowdive")
    assert len(mb) == n
