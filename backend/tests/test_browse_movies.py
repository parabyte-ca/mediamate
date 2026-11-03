"""Browse → Movies: TMDB lists and search, the library flag, and the no-key case."""
import pytest

from app.models import ContentRating, Setting
from app.services import browse
from app.services.media import store

from tests.test_doctor import api, clip, lib  # noqa: F401


@pytest.fixture(autouse=True)
def fresh_cache():
    browse._movie_cache.clear()


def test_without_a_tmdb_key_it_asks_for_one(api):  # noqa: F811
    client, *_ = api
    assert client.get("/api/browse/movies").json() == {"need_key": True, "movies": [], "list": "popular"}


def test_lists_search_and_library_flag(api, monkeypatch):  # noqa: F811
    client, Session, media, index = api
    with Session() as s:
        s.add(Setting(key="tmdb_api_key", value="0" * 32))
        s.add(Setting(key="browse_country", value="CA"))
        s.add(ContentRating(kind="movie", title_key="dune", year=2021, title="Dune", cert="PG-13"))
        s.commit()
    index(clip(media / "Movies" / "Dune (2021)" / "Dune (2021).mkv"))
    calls = []

    async def fake_get(key, path, params=None):
        calls.append((path, params))
        return {"results": [
            {"id": 1, "title": "Dune", "release_date": "2021-10-22", "poster_path": "/d.jpg", "vote_average": 7.8,
             "overview": "Spice."},
            {"id": 2, "title": "Dune", "release_date": "1984-12-14", "poster_path": "/d84.jpg", "vote_average": 6.3},
            {"id": 3, "title": "No Poster", "release_date": "2020-01-01"},
        ]}
    monkeypatch.setattr("app.services.tmdb._get", fake_get)

    r = client.get("/api/browse/movies?list=upcoming").json()
    assert calls[0] == ("/movie/upcoming", {"region": "CA"})
    assert [(m["title"], m["year"], m["in_library"], m["certification"]) for m in r["movies"]] == [
        ("Dune", 2021, True, "PG-13"), ("Dune", 1984, False, None)]      # no-poster entries dropped from lists
    assert r["movies"][0]["poster_url"].endswith("/d.jpg") and r["movies"][0]["rating"] == 7.8

    client.get("/api/browse/movies?list=upcoming")
    assert len(calls) == 1                                                  # cached

    s = client.get("/api/browse/movies?q=dune").json()
    assert s["list"] == "search" and calls[-1][0] == "/search/movie" and len(s["movies"]) == 3


def test_tmdb_down_is_a_clear_502(api, monkeypatch):  # noqa: F811
    client, Session, *_ = api
    with Session() as s:
        s.add(Setting(key="tmdb_api_key", value="0" * 32))
        s.commit()
    from app.services import tmdb

    async def down(*a, **k):
        raise tmdb.TMDBUnavailable("HTTP 503")
    monkeypatch.setattr("app.services.tmdb._get", down)
    r = client.get("/api/browse/movies")
    assert r.status_code == 502 and "TMDB" in r.json()["detail"]
