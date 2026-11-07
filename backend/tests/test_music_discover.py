"""Browse → Music: ListenBrainz new and popular releases, a taste profile from your
library and followed artists, "for you" ranking, and Add (follow + want this album)."""
import pytest
from fastapi.testclient import TestClient

from app.models import MusicAlbum, MusicLibAlbum, WatchlistItem
from app.services import music_discover as md
from app.services import musicbrainz

from tests.test_music_watchlist import ARTIST, GROUPS, _r, mb  # noqa: F401
from tests.test_watchlist_movies import Session, db, grabbing  # noqa: F401

FRESH = {"payload": {"releases": [
    {"release_group_mbid": "rg-new-1", "release_name": "Glow", "artist_credit_name": "Beach House",
     "artist_mbids": ["a-bh"], "release_date": "2026-11-01", "release_group_primary_type": "Album",
     "caa_id": 7, "caa_release_mbid": "rel-1", "release_tags": ["dream pop", "shoegaze"]},
    {"release_group_mbid": "rg-new-2", "release_name": "Bangers", "artist_credit_name": "Some EDM Act",
     "artist_mbids": ["a-edm"], "release_date": "2026-11-03", "release_group_primary_type": "Album",
     "release_tags": ["edm"]},
    {"release_group_mbid": "rg-new-3", "release_name": "Hits Live", "artist_credit_name": "Phoebe Bridgers",
     "artist_mbids": ["art-1"], "release_date": "2026-11-02", "release_group_primary_type": "Album",
     "release_group_secondary_type": "Live"},
    {"release_group_mbid": "rg-new-4", "release_name": "Single", "artist_credit_name": "X",
     "artist_mbids": ["a-x"], "release_date": "2026-11-02", "release_group_primary_type": "Single"},
]}}
POPULAR = {"payload": {"release_groups": [
    {"release_group_mbid": "rg-2", "release_group_name": "Punisher", "artist_name": "Phoebe Bridgers",
     "artist_mbids": ["art-1"], "listen_count": 900},
    {"release_group_mbid": "rg-pop-2", "release_group_name": "Loud", "artist_name": "Metal Band",
     "artist_mbids": ["a-metal"], "listen_count": 800},
]}}
TAGS = {"Slowdive": {"shoegaze": 5, "dream pop": 3}, "Mazzy Star": {"dream pop": 4},
        "Metal Band": {"metal": 9}, "Some EDM Act": {"edm": 3}}


@pytest.fixture
def lb(monkeypatch):
    md._cache.clear()
    monkeypatch.setattr(md, "SYNC", True)
    monkeypatch.setattr(md, "_lb", lambda path, **p: FRESH if "fresh" in path else POPULAR)

    def fake_get(path, **params):
        if path == "/artist":
            name = params["query"].split('"')[1]
            return {"artists": [{"id": "id-" + name, "score": 100,
                                 "tags": [{"name": t, "count": c} for t, c in TAGS.get(name, {}).items()]}]}
        return {"tags": [{"name": "indie folk", "count": 3}]}
    monkeypatch.setattr(musicbrainz, "_get", fake_get)


@pytest.fixture
def mine(db):
    for artist, n in (("Slowdive", 4), ("Mazzy Star", 2)):
        for i in range(n):
            db.add(MusicLibAlbum(folder=f"/m/{artist}/{i}", artist=artist, title=f"Album {i}", format="flac"))
    db.commit()
    return db


def test_new_releases_are_parsed_without_live_or_singles(lb):
    rows = md._fresh()
    assert [r["title"] for r in rows] == ["Bangers", "Glow"]
    assert rows[1]["cover"] == "https://coverartarchive.org/release/rel-1/7-250.jpg"
    assert rows[1]["tags"] == ["dream pop", "shoegaze"]


def test_taste_from_your_library(lb, mine):
    md.build_taste(mine.get_bind())
    t = md.taste(mine)
    assert set(t) == {"shoegaze", "dream pop"} and t["dream pop"] > t["shoegaze"]


def test_for_you_ranks_and_hides(lb, mine):
    md.build_taste(mine.get_bind())
    r = md.rank(mine, md.releases("new", mine.get_bind()), for_you=True)
    assert [x["title"] for x in r["releases"]] == ["Glow"]
    assert r["releases"][0]["why"].startswith("Matches: dream pop")
    every = md.rank(mine, md.releases("new", mine.get_bind()), for_you=False)
    assert {x["title"] for x in every["releases"]} == {"Glow", "Bangers"}


def test_artists_you_have_come_first(lb, mine):
    mine.add(MusicLibAlbum(folder="/m/pb", artist="Phoebe Bridgers", title="Punisher", format="flac"))
    mine.commit()
    md.build_taste(mine.get_bind())
    r = md.rank(mine, md.releases("popular", mine.get_bind()), for_you=True)
    first = r["releases"][0]
    assert first["title"] == "Punisher" and first["in_library"] and first["why"] == "You have 1 album by them"
    assert all(x["title"] != "Loud" for x in r["releases"])              # metal: nothing in common


def test_add_follows_and_wants_only_this_album(db, mb, grabbing, lb):
    grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]")]
    out = md.add(db, "art-1", "rg-2", "Punisher")
    assert out["message"].startswith("Following Phoebe Bridgers. Started")
    st = {a.title: a.status for a in db.query(MusicAlbum)}
    assert st == {"Stranger in the Alps": "ignored", "Punisher": "grabbed", "Next One": "wanted"}
    grabbing["results"].append(_r("Phoebe Bridgers - Stranger in the Alps [FLAC]", "d"))
    out = md.add(db, "art-1", "rg-1", "Stranger in the Alps")              # already following: just want it
    assert not out["message"].startswith("Following") and db.query(WatchlistItem).count() == 1
    assert db.query(MusicAlbum).filter_by(mbid="rg-1").one().status == "grabbed"


def test_routes(Session, lb, mb, grabbing):
    from app.database import get_db
    from app.main import app

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _db
    try:
        c = TestClient(app)
        r = c.get("/api/browse/music?list=new&for_you=false").json()
        assert {x["title"] for x in r["releases"]} == {"Glow", "Bangers"} and r["taste_ready"] is False
        assert c.get("/api/browse/music?list=bad").status_code == 400
        grabbing["results"] = [_r("Phoebe Bridgers - Punisher [FLAC]")]
        out = c.post("/api/browse/music/add", json={"artist_mbid": "art-1", "mbid": "rg-2", "title": "Punisher"})
        assert out.status_code == 200 and out.json()["album_status"] == "grabbed"
        assert c.post("/api/browse/music/add", json={}).status_code == 400
        r = c.get("/api/browse/music?list=popular&for_you=false").json()
        pun = next(x for x in r["releases"] if x["title"] == "Punisher")
        assert pun["following"] and pun["status"] == "grabbed"
    finally:
        app.dependency_overrides.pop(get_db, None)
