"""Kids: PG-13 / TV-14 horror goes to Mature, your per-title choice wins, and the
Library can move a whole title (all its episodes) in one go."""
from unittest.mock import patch

import pytest

from app.models import AudienceOverride, Setting
from app.services import movie_genres, organise, ratings

from tests.test_doctor import clip, lib  # noqa: F401
from tests.test_ratings import db  # noqa: F401


def _fakes(cert, genres=(), keywords=()):
    async def g(*a, **k):
        return list(genres)

    async def kw(*a, **k):
        return list(keywords)
    return (patch.object(ratings, "fetch", side_effect=lambda *a, **k: cert),
            patch.object(movie_genres, "_fetch", side_effect=g),
            patch.object(ratings, "_tv_keywords", side_effect=kw))


def _plan(s, path, cert, genres=(), keywords=()):
    a, b, c = _fakes(cert, genres, keywords)
    with a, b, c:
        return organise.plan(s, path)


@pytest.mark.parametrize("cert,genres,expect", [
    ("PG-13", ["Horror", "Mystery"], ("mature", "rated PG-13, but horror")),
    ("PG", ["Horror", "Comedy", "Family"], ("general", "rated PG")),
    ("PG-13", ["Drama"], ("general", "rated PG-13")),
    ("14A", ["Thriller", "Horror"], ("mature", "rated 14A, but horror")),
    (None, ["Horror"], ("mature", "no rating found, so Mature to be safe")),
])
def test_movie_horror_rule(db, cert, genres, expect):
    s, _, _ = db
    p = _plan(s, "/dl/The.Ring.2002.1080p.mkv", cert, genres)
    assert (p["audience"], p["reason"]) == expect


@pytest.mark.parametrize("cert,expect", [
    ("TV-14", ("mature", "rated TV-14")), ("TV-MA", ("mature", "rated TV-MA")),
    ("TV-PG", ("general", "rated TV-PG")), ("TV-Y7", ("general", "rated TV-Y7")),
])
def test_tv_14_and_up_go_to_mature_tv(db, cert, expect):
    s, media, _ = db
    p = _plan(s, "/dl/Some.Show.S01E01.mkv", cert)
    assert (p["audience"], p["reason"]) == expect
    folder = "organise_tv_mature_dir" if expect[0] == "mature" else "organise_tv_dir"
    assert p["target"].startswith(str(media / folder))


def test_movie_switch_turns_the_horror_rule_off(db):
    s, _, _ = db
    s.add(Setting(key="kids_no_horror", value="false"))
    s.commit()
    assert _plan(s, "/dl/The.Ring.2002.mkv", "PG-13", ["Horror"])["audience"] == "general"


def test_override_beats_rating_and_watchlist_beats_override(db):
    from app.models import WatchlistItem
    s, _, _ = db
    ratings.set_override(s, "movie", "The Ring", 2002, "general")
    assert _plan(s, "/dl/The.Ring.2002.mkv", "PG-13", ["Horror"])["reason"] == "your choice (Kid-friendly)"
    ratings.set_override(s, "movie", "The Ring", 2002, "mature")
    assert s.query(AudienceOverride).count() == 1
    ratings.set_override(s, "tv", "Bluey", None, "mature")
    s.add(WatchlistItem(title="Bluey", search_query="bluey", audience="general"))
    s.commit()
    assert _plan(s, "/dl/Bluey.S01E01.mkv", "TV-Y")["reason"] == "set on the Watchlist"


def test_move_a_whole_show_to_mature(db, lib, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import doctor
    s, media, index = db
    monkeypatch.setattr("app.database.SessionLocal", lib[0])
    refreshed = []
    monkeypatch.setattr(doctor, "refresh_media_servers", lambda _db: refreshed.append(1))
    a = index(clip(media / "organise_tv_dir" / "Wednesday" / "Season 01" / "Wednesday - S01E01.mkv"))  # TV-14
    index(clip(media / "organise_tv_dir" / "Wednesday" / "Season 01" / "Wednesday - S01E02.mkv"))
    client = TestClient(app)
    with patch.object(ratings, "fetch", side_effect=AssertionError("no lookup: your choice wins")):
        r = client.post(f"/api/media/files/{a}/audience", json={"audience": "mature"}).json()
    assert r["moved"] == 2 and "Wednesday: moved 2 files to Mature" in r["msg"] and refreshed == [1]
    assert (media / "organise_tv_mature_dir" / "Wednesday" / "Season 01" / "Wednesday - S01E02.mkv").exists()
    assert client.post(f"/api/media/files/{a}/audience", json={"audience": "x"}).status_code == 422
    assert client.post("/api/media/files/99999/audience", json={"audience": "mature"}).status_code == 404


def test_doctor_move_all_only_goes_towards_mature(db):
    from app.services import doctor
    s, media, index = db
    index(clip(media / "organise_movie_dir" / "The Ring (2002)" / "The Ring (2002).mkv"))
    index(clip(media / "organise_movie_mature_dir" / "Paddington (2014)" / "Paddington (2014).mkv"))
    async def genres(key, title, year):
        return ["Horror"] if "Ring" in title else ["Family", "Comedy"]
    with patch.object(ratings, "fetch", side_effect=lambda key, kind, title, *x, **k: "PG-13" if "Ring" in title else "PG"), \
            patch.object(movie_genres, "_fetch", side_effect=genres):
        issues = {i["path"].rsplit("/", 1)[-1]: i for i in doctor.check(s)["issues"] if i["kind"] == "audience"}
    ring, pad = issues["The Ring (2002).mkv"], issues["Paddington (2014).mkv"]
    assert "rated PG-13, but horror" in ring["detail"] and ring["fix"] == "Move to Mature" and "bulk" not in ring
    assert pad["fix"] == "Move to regular" and pad["bulk"] is False
