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
    import json as _json
    s.merge(Setting(key="doctor_report", value=_json.dumps({"issues": [
        {"id": "a", "kind": "audience", "path": "/x/Heat (1995)/Heat (1995).mkv"},
        {"id": "b", "kind": "audience", "path": "/x/Other (2001)/Other (2001).mkv"}], "total": 2, "fixed": []})))
    s.commit()
    assert doctor.forget_audience(s, ratings._key("Heat"), "movie") == 1
    assert (media / "organise_tv_mature_dir" / "Wednesday" / "Season 01" / "Wednesday - S01E02.mkv").exists()
    assert client.post(f"/api/media/files/{a}/audience", json={"audience": "x"}).status_code == 422
    assert client.post("/api/media/files/99999/audience", json={"audience": "mature"}).status_code == 404


def test_where_it_is_is_your_decision(db):
    """Files already in a folder stay; only a clearly adult rating in a regular folder is
    raised (with Keep here), and nothing is ever suggested from Mature to regular."""
    from app.services import doctor
    s, media, index = db
    index(clip(media / "organise_movie_dir" / "The Ring (2002)" / "The Ring (2002).mkv"))          # PG-13 horror
    index(clip(media / "organise_movie_mature_dir" / "Paddington (2014)" / "Paddington (2014).mkv"))  # PG
    index(clip(media / "organise_tv_dir" / "Andor" / "Season 01" / "Andor - S01E01.mkv"))           # TV-14
    index(clip(media / "organise_tv_mature_dir" / "The Haunting Hour" / "Season 01" /
               "The Haunting Hour - S01E01.mkv"))                                                      # TV-PG
    index(clip(media / "organise_movie_dir" / "Heat (1995)" / "Heat (1995).mkv"))                    # R
    certs = {"The Ring": "PG-13", "Paddington": "PG", "Andor": "TV-14", "The Haunting Hour": "TV-PG", "Heat": "R"}

    async def genres(key, title, year):
        return ["Horror"] if "Ring" in title else ["Drama"]
    with patch.object(ratings, "fetch", side_effect=lambda key, kind, title, *x, **k: certs.get(title)), \
            patch.object(movie_genres, "_fetch", side_effect=genres):
        issues = [i for i in doctor.check(s)["issues"] if i["kind"] == "audience"]
        assert [i["path"].rsplit("/", 1)[-1] for i in issues] == ["Heat (1995).mkv"]
        assert issues[0]["keep_here"] is True and "rated R" in issues[0]["detail"]
        ratings.set_override(s, "movie", "Heat", 1995, "general")                 # Keep here
        assert [i for i in doctor.check(s)["issues"] if i["kind"] == "audience"] == []


def test_new_episodes_follow_their_show(db):
    s, media, _ = db
    (media / "organise_tv_mature_dir" / "The Haunting Hour").mkdir(parents=True)
    (media / "organise_tv_dir" / "Andor").mkdir(parents=True)
    with patch.object(ratings, "fetch", side_effect=AssertionError("the show's folder decides")):
        h = organise.plan(s, "/dl/The.Haunting.Hour.S02E01.mkv")
        a = organise.plan(s, "/dl/Andor.S02E01.mkv")
    assert (h["audience"], h["reason"]) == ("mature", "the show is already in the Mature folder")
    assert (a["audience"], a["reason"]) == ("general", "the show is already in the regular folder")
    assert _plan(s, "/dl/Brand.New.Show.S01E01.mkv", "TV-14")["audience"] == "mature"     # new: by rating
