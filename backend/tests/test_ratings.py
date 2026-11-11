"""Regular vs Mature folders: rating classes, TMDB lookups (cached), the watchlist
override, routing in Rename & organise, and the doctor's wrong-folder check."""
import asyncio
from unittest.mock import patch

import pytest

from app.models import ContentRating, Setting, WatchlistItem
from app.services import organise, ratings

from tests.test_doctor import clip, lib  # noqa: F401
from tests.test_organise import S as BASE


@pytest.mark.parametrize("cert,level", [
    ("PG-13", "general"), ("TV-14", "general"), ("G", "general"), ("TV-Y7", "general"), ("14A", "general"),
    ("R", "mature"), ("TV-MA", "mature"), ("NC-17", "mature"), ("18A", "mature"),
    ("NR", None), ("", None), (None, None),
])
def test_classify(cert, level):
    assert ratings.classify(cert) == level


def test_fetch_prefers_us_then_canada(monkeypatch):
    async def fake_get(key, path, params=None):
        if path == "/search/tv":
            return {"results": [{"id": 7}]}
        if path == "/tv/7/content_ratings":
            return {"results": [{"iso_3166_1": "DE", "rating": "16"}, {"iso_3166_1": "CA", "rating": "14+"}]}
        if path == "/search/movie":
            return {"results": [{"id": 9}]}
        if path == "/movie/9/release_dates":
            return {"results": [{"iso_3166_1": "US", "release_dates": [{"certification": ""}, {"certification": "R"}]}]}
        raise AssertionError(path)
    monkeypatch.setattr(ratings.tmdb, "_get", fake_get)
    assert asyncio.run(ratings.fetch("k", "tv", "Show")) == "14+"
    assert asyncio.run(ratings.fetch("k", "movie", "Film", 2020)) == "R"


S = dict(BASE, organise_tv_mature_dir="/mtv", organise_movie_mature_dir="/mmovies")


def test_targets_follow_the_audience():
    assert organise.target_for(S, "/dl/Show.S01E01.mkv", audience="mature")[0] == "/mtv/Show/Season 01/Show - S01E01.mkv"
    assert organise.target_for(S, "/dl/Film.2020.mkv", audience="general")[0] == "/movies/Film (2020)/Film (2020).mkv"
    # Without Mature folders, everything uses the regular ones (as before).
    assert organise.target_for(BASE, "/dl/Film.2020.mkv", audience="mature")[0].startswith("/movies/")


@pytest.fixture
def db(lib):  # noqa: F811
    Session, media, index = lib
    with Session() as s:
        for k in ("organise_tv_dir", "organise_movie_dir", "organise_tv_mature_dir", "organise_movie_mature_dir"):
            s.add(Setting(key=k, value=str(media / k)))
        s.add(Setting(key="tmdb_api_key", value="0" * 32))
        s.commit()
        yield s, media, index


def test_plan_uses_rating_then_caches_it(db):
    s, media, _ = db
    with patch.object(ratings, "fetch", return_value=None) as f:
        f.side_effect = lambda *a, **k: "TV-MA"
        p = organise.plan(s, "/dl/Severance.S01E01.mkv")
        assert (p["audience"], p["reason"]) == ("mature", "rated TV-MA")
        assert p["target"].startswith(str(media / "organise_tv_mature_dir"))
        organise.plan(s, "/dl/Severance.S01E02.mkv")
        assert f.call_count == 1                                     # cached
    assert s.query(ContentRating).one().cert == "TV-MA"


def test_kid_friendly_and_unknown(db):
    s, media, _ = db
    with patch.object(ratings, "fetch", side_effect=lambda key, kind, *a, **k: ("PG-13" if kind == "movie" else None)):
        film = organise.plan(s, "/dl/Spider-Man.2002.mkv")
        show = organise.plan(s, "/dl/Obscure.Show.S01E01.mkv")
    assert film["audience"] == "general" and film["target"].startswith(str(media / "organise_movie_dir"))
    assert (show["audience"], show["reason"]) == ("mature", "no rating found, so Mature to be safe")


def test_watchlist_choice_and_override_win(db):
    s, media, _ = db
    s.add(WatchlistItem(title="Bluey", search_query="bluey", audience="general"))
    s.commit()
    with patch.object(ratings, "fetch", side_effect=AssertionError("no lookup needed")):
        p = organise.plan(s, "/dl/bluey.s02e03.mkv")
        assert (p["audience"], p["reason"], p["show_hint"]) == ("general", "set on the Watchlist", "Bluey")
        assert organise.plan(s, "/dl/bluey.s02e03.mkv", override="mature")["audience"] == "mature"


def test_no_tmdb_key_means_mature(db):
    s, _, _ = db
    s.query(Setting).filter_by(key="tmdb_api_key").delete()
    s.commit()
    assert organise.plan(s, "/dl/Film.2020.mkv")["audience"] == "mature"


def test_doctor_flags_a_mature_title_in_the_regular_folder(db):
    from app.services import doctor
    s, media, index = db
    f = clip(media / "organise_movie_dir" / "Heat (1995)" / "Heat (1995).mkv")
    index(f)
    with patch.object(ratings, "fetch", side_effect=lambda *a, **k: "R"):
        [issue] = doctor.check(s)["issues"]
        assert issue["kind"] == "audience" and "rated R" in issue["detail"] and issue["keep_here"] is True
        assert doctor.fix(s, issue).startswith("Moved to")
    assert (media / "organise_movie_mature_dir" / "Heat (1995)" / "Heat (1995).mkv").exists()

