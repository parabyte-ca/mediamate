"""Show matching: pick the show a person means, not TVMaze's fuzzy top hit,
and let them change a wrong match from the Watchlist."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import WatchlistItem
from app.services import show_metadata, tvmaze


def s(id_, name, premiered="2017-01-01", language="English", **kw):
    return {"id": id_, "name": name, "premiered": premiered, "language": language, "status": "Ended",
            "image": {"medium": f"https://static.tvmaze.com/{id_}.jpg"}, "summary": f"<p>{name}</p>",
            "network": {"name": "NBC"}, **kw}


GONE_DARK = s(1, "I'll Be Gone in the Dark", "2020-06-28")
GONE = s(2, "Gone", "2017-10-26")
GONE_FR = s(3, "Gone", "2012-01-01", language="French")


class TestPickBest:
    def test_exact_name_beats_tvmazes_top_hit(self):
        assert tvmaze.pick_best([GONE_DARK, GONE], "Gone")["id"] == 2
        assert tvmaze.pick_best([GONE_DARK, GONE], "gone")["id"] == 2

    def test_same_name_year_hint_then_english(self):
        assert tvmaze.pick_best([GONE_FR, GONE], "Gone")["id"] == 2
        assert tvmaze.pick_best([GONE, GONE_FR], "Gone", 2012)["id"] == 3

    def test_year_hint_excludes_other_years(self):
        assert tvmaze.pick_best([GONE_DARK, GONE], "Gone", 1999) is None

    def test_suffixes_articles_and_ampersands(self):
        office = s(5, "The Office", "2005-03-24")
        uk = s(6, "The Office (UK)", "2001-07-09")
        assert tvmaze.pick_best([uk, office], "Office")["id"] == 6       # TVMaze order breaks the tie
        assert tvmaze.pick_best([uk, office], "The Office", 2005)["id"] == 5
        assert tvmaze.pick_best([s(7, "Law and Order"), s(8, "Law & Order")], "Law & Order")["id"] == 7

    def test_starts_with_then_contains_then_tvmaze_order(self):
        shows = [s(1, "Breaking Badly Funny"), s(2, "Bad Breaking"), s(3, "Breaking Bad Habits")]
        assert tvmaze.pick_best(shows, "Breaking Bad")["id"] == 3
        assert tvmaze.pick_best([s(1, "Zzz"), s(2, "Yyy")], "Brekaing Bad")["id"] == 1   # typo: keep TVMaze's guess

    def test_numeric_names(self):
        assert tvmaze.pick_best([s(1, "1883 Origins"), s(2, "1883", "2021-12-19")], "1883")["id"] == 2
        assert tvmaze.pick_best([s(1, "9-1-1: Lone Star"), s(2, "9-1-1")], "9-1-1")["id"] == 2

    def test_empty(self):
        assert tvmaze.pick_best([], "Gone") is None

    def test_lookup_by_name_uses_the_list(self):
        with patch("app.services.tvmaze.lookup_many", new=AsyncMock(return_value=[GONE_DARK, GONE])):
            assert asyncio.run(tvmaze.lookup_by_name("Gone"))["id"] == 2

    def test_find_show_with_year_ranks_too(self):
        with patch("app.services.tvmaze.lookup_many",
                   new=AsyncMock(return_value=[s(9, "Gone Girl Diaries", "2017-01-01"), GONE])):
            assert asyncio.run(show_metadata.find_show(["Gone 2017"]))["id"] == 2


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def api(db):
    from app.routers import watchlist
    app = FastAPI()
    app.include_router(watchlist.router, prefix="/api")

    def _override():
        yield db
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _item(db, **kw):
    base = dict(title="Gone", search_query="Gone", season=1, episode=1, tvmaze_id=1,
                poster_url="https://static.tvmaze.com/1.jpg", summary="Wrong show", network="HBO",
                show_status="Ended")
    base.update(kw)
    w = WatchlistItem(**base)
    db.add(w)
    db.commit()
    return w


class TestChangeMatch:
    def test_lists_candidates_best_first(self, api, db):
        w = _item(db)
        with patch("app.services.tvmaze.lookup_many", new=AsyncMock(return_value=[GONE_DARK, GONE])) as many:
            r = api.get(f"/api/watchlist/{w.id}/matches")
        assert r.status_code == 200
        body = r.json()
        many.assert_awaited_once_with("Gone")
        assert body["current"] == 1 and body["query"] == "Gone"
        assert [c["tvmaze_id"] for c in body["shows"]] == [2, 1]

    def test_custom_query(self, api, db):
        w = _item(db)
        with patch("app.services.tvmaze.lookup_many", new=AsyncMock(return_value=[])) as many:
            api.get(f"/api/watchlist/{w.id}/matches", params={"q": "Gone NBC"})
        many.assert_awaited_once_with("Gone NBC")

    def test_unavailable_is_503(self, api, db):
        w = _item(db)
        with patch("app.services.tvmaze.lookup_many", new=AsyncMock(side_effect=tvmaze.TVMazeUnavailable("x"))):
            assert api.get(f"/api/watchlist/{w.id}/matches").status_code == 503

    def test_set_match_replaces_metadata(self, api, db):
        w = _item(db)
        with patch("app.services.tvmaze.fetch_show", new=AsyncMock(return_value=s(2, "Gone", status="Running", network=None))):
            r = api.post(f"/api/watchlist/{w.id}/match", json={"tvmaze_id": 2})
        assert r.status_code == 200
        body = r.json()
        assert body["tvmaze_id"] == 2 and body["poster_url"].endswith("/2.jpg")
        assert body["network"] is None and body["summary"] == "Gone"     # nothing left from the wrong show
        assert body["show_status"] == "Running" and body["title"] == "Gone"

    def test_set_match_renames_when_the_show_differs(self, api, db):
        w = _item(db, title="I'll Be Gone in the Dark", search_query="I'll Be Gone in the Dark")
        with patch("app.services.tvmaze.fetch_show", new=AsyncMock(return_value=s(6, "The Office (UK)"))):
            body = api.post(f"/api/watchlist/{w.id}/match", json={"tvmaze_id": 6}).json()
        assert body["title"] == "The Office (UK)" and body["search_query"] == "The Office"

    def test_already_on_the_watchlist_is_409(self, api, db):
        w = _item(db)
        _item(db, title="Gone (2017)", tvmaze_id=2)
        assert api.post(f"/api/watchlist/{w.id}/match", json={"tvmaze_id": 2}).status_code == 409

    def test_unknown(self, api, db):
        assert api.post("/api/watchlist/999/match", json={"tvmaze_id": 2}).status_code == 404
        assert api.get("/api/watchlist/999/matches").status_code == 404
        w = _item(db)
        with patch("app.services.tvmaze.fetch_show", new=AsyncMock(return_value=None)):
            assert api.post(f"/api/watchlist/{w.id}/match", json={"tvmaze_id": 5}).status_code == 404


def test_status_check_never_replaces_a_known_match(db):
    from app.services.scheduler import _check_statuses_async
    w = _item(db, tvmaze_id=2, show_status=None)
    with patch("app.services.tvmaze.get_show_status", new=AsyncMock(return_value=("Running", 1))):
        asyncio.run(_check_statuses_async(db))
    db.refresh(w)
    assert w.tvmaze_id == 2 and w.show_status == "Running"


def test_add_with_a_picked_show_skips_guessing(api, db):
    with patch("app.services.tvmaze.fetch_show", new=AsyncMock(return_value=GONE)) as fetch, \
         patch("app.services.tvmaze.lookup_many", new=AsyncMock(return_value=[GONE_DARK])) as many:
        r = api.post("/api/watchlist", json={"title": "Gone", "search_query": "Gone", "tvmaze_id": 2})
    assert r.status_code == 201 and r.json()["tvmaze_id"] == 2 and r.json()["network"] == "NBC"
    fetch.assert_awaited_once_with(2)
    many.assert_not_awaited()
    assert api.post("/api/watchlist", json={"title": "Gone", "search_query": "Gone", "tvmaze_id": 2}).status_code == 409


def test_browse_search_puts_the_exact_name_first():
    from app.services import browse
    with patch("app.services.tvmaze.search_shows", new=AsyncMock(return_value=[GONE_DARK, GONE])):
        cards = asyncio.run(browse.search("Gone"))
    assert [c["tvmaze_id"] for c in cards] == [2, 1]
