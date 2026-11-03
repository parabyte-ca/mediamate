"""Movies on the watchlist: release dates, adding from TMDB, scanning, completion, removal."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.models import Download, Setting, WatchlistEpisode, WatchlistItem
from app.services import journeys, movies, tmdb

KEY = "a" * 32
TODAY = datetime.now(timezone.utc).date()


def d(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


@pytest.fixture
def Session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    journeys.install_download_hooks()
    monkeypatch.setattr(journeys, "PIPELINE_SYNC", True)
    monkeypatch.setattr("app.services.pipeline.on_download_complete", lambda bind, dl_id: None)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def db(Session):
    s = Session()
    yield s
    s.close()


def _movie(db, **kw):
    base = dict(kind="movie", title="Dune Part Three", search_query="Dune Part Three", year=2026,
                tmdb_id=99, release_date=d(-1), release_kind="digital", season=1, episode=1)
    base.update(kw)
    w = WatchlistItem(**base)
    db.add(w)
    db.commit()
    return w


def _result(title="Dune.Part.Three.2026.1080p.WEB-DL.x265-GRP", h="a", seeds=20, size=5):
    return {"title": title, "seeds": seeds, "size_bytes": size, "source": "tpb",
            "magnet": "magnet:?xt=urn:btih:" + h * 40, "info_hash": h * 40}


@pytest.fixture
def grabbing():
    ctx = {"adds": [], "results": [_result()], "queries": []}

    def search(query, **kw):
        ctx["queries"].append((query, kw))
        return [dict(r) for r in ctx["results"]]

    def fake_add(magnet, save_path, category=None):
        ctx["adds"].append((magnet, save_path))
        return magnet[-40:]

    with patch("app.services.indexers.search_all", side_effect=search), \
         patch("app.services.qbittorrent.add_torrent", side_effect=fake_add), \
         patch("app.services.apprise_notify.notify") as notify:
        ctx["notify"] = notify
        yield ctx


# ── release dates ────────────────────────────────────────────────────────────


def rel(country, *entries):
    return {"iso_3166_1": country, "release_dates": [{"type": t, "release_date": f"{day}T00:00:00.000Z"}
                                                     for t, day in entries]}


class TestPickRelease:
    def test_digital_first_then_physical(self):
        res = [rel("US", (3, "2026-03-01"), (5, "2026-05-20"), (4, "2026-05-01"))]
        assert tmdb.pick_release(res, "US") == ("2026-05-01", "digital")
        assert tmdb.pick_release([rel("US", (3, "2026-03-01"), (5, "2026-05-20"))], "US") == ("2026-05-20", "physical")

    def test_own_country_then_us(self):
        res = [rel("US", (4, "2026-05-01")), rel("CA", (4, "2026-05-08"))]
        assert tmdb.pick_release(res, "CA") == ("2026-05-08", "digital")
        assert tmdb.pick_release([rel("US", (4, "2026-05-01"))], "CA") == ("2026-05-01", "digital")

    def test_estimate_from_cinemas(self):
        assert tmdb.pick_release([rel("US", (3, "2026-03-01"))], "US") == ("2026-05-30", "estimated")
        assert tmdb.pick_release([], "US", "2026-01-01") == ("2026-04-01", "estimated")
        assert tmdb.pick_release([], "US") == (None, None)

    def test_garbage(self):
        assert tmdb.pick_release([None, {"release_dates": "x"}, rel("US", (4, "soon"))], "US", "bad") == (None, None)


# ── adding ───────────────────────────────────────────────────────────────────


@pytest.fixture
def api(db):
    from app.routers import browse, watchlist
    app = FastAPI()
    app.include_router(watchlist.router, prefix="/api")
    app.include_router(browse.router, prefix="/api")

    def _override():
        yield db
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _key(db):
    db.add(Setting(key="tmdb_api_key", value=KEY))
    db.commit()


MOVIE = {"id": 99, "title": "Dune: Part Three", "release_date": "2026-12-18", "poster_path": "/p.jpg",
         "overview": "Paul again."}


class TestAddFromTmdb:
    def test_creates_a_movie_waiting_for_release(self, api, db):
        _key(db)
        with patch("app.services.tmdb.movie", new=AsyncMock(return_value=MOVIE)), \
             patch("app.services.tmdb.release_info", new=AsyncMock(return_value=("2027-03-02", "digital"))):
            r = api.post("/api/watchlist/from-tmdb", json={"tmdb_id": 99})
        assert r.status_code == 201
        body = r.json()
        assert body["kind"] == "movie" and body["tmdb_id"] == 99 and body["year"] == 2026
        assert body["release_date"] == "2027-03-02" and body["release_kind"] == "digital"
        assert body["title"] == "Dune: Part Three" and body["poster_url"].endswith("/p.jpg")
        listed = api.get("/api/watchlist").json()[0]
        assert listed["movie_state"] == "waiting" and listed["removes_at"] is None

    def test_duplicate_and_no_key(self, api, db):
        assert api.post("/api/watchlist/from-tmdb", json={"tmdb_id": 99}).status_code == 400
        _key(db)
        _movie(db)
        assert api.post("/api/watchlist/from-tmdb", json={"tmdb_id": 99}).status_code == 409

    def test_tmdb_down_is_502(self, api, db):
        _key(db)
        with patch("app.services.tmdb.movie", new=AsyncMock(side_effect=tmdb.TMDBUnavailable("x"))):
            assert api.post("/api/watchlist/from-tmdb", json={"tmdb_id": 5}).status_code == 502

    def test_browse_marks_movies_on_the_watchlist(self, api, db):
        _key(db)
        w = _movie(db)
        cards = [{"tmdb_id": 99, "title": "Dune Part Three", "year": 2026}, {"tmdb_id": 7, "title": "X", "year": 2026}]
        with patch("app.services.browse.movies", new=AsyncMock(return_value=cards)), \
             patch("app.services.media.store.get_all_files", return_value=[]):
            out = api.get("/api/browse/movies", params={"list": "upcoming"}).json()["movies"]
        assert out[0]["on_watchlist"] and out[0]["watchlist_id"] == w.id
        assert not out[1]["on_watchlist"]

    def test_typed_movie_never_asks_tvmaze(self, api, db):
        _key(db)
        with patch("app.services.tvmaze.lookup_many", new=AsyncMock()) as tv, \
             patch("app.services.tmdb.search_movie", new=AsyncMock(return_value=MOVIE)), \
             patch("app.services.tmdb.release_info", new=AsyncMock(return_value=("2027-03-02", "digital"))):
            r = api.post("/api/watchlist", json={"title": "Dune Part Three", "search_query": "Dune Part Three",
                                                 "kind": "movie"})
        body = r.json()
        tv.assert_not_awaited()
        assert body["kind"] == "movie" and body["tmdb_id"] == 99 and body["release_date"] == "2027-03-02"


# ── scanning ─────────────────────────────────────────────────────────────────


class TestGrab:
    def test_waits_for_the_release(self, db, grabbing):
        w = _movie(db, release_date=d(10))
        assert movies.grab(db, w)["status"] == "waiting"
        assert grabbing["queries"] == [] and movies.state(db, w) == "waiting"

    def test_searches_title_and_year_and_grabs(self, db, grabbing):
        w = _movie(db)
        grabbing["results"] = [
            _result("Dune Part Three S01E01 1080p", h="b", seeds=99),     # an episode
            _result("Dune Part Three 1984 1080p", h="c", seeds=90),       # another film
            _result(h="a", seeds=20),
        ]
        out = movies.grab(db, w)
        assert out["status"] == "started" and "2026" in out["title"]
        assert grabbing["queries"][0][0] == "Dune Part Three 2026"
        dl = db.query(Download).one()
        assert dl.watchlist_id == w.id and db.query(WatchlistEpisode).count() == 0
        assert movies.state(db, w) == "downloading"
        assert movies.grab(db, w)["status"] == "already"

    def test_force_searches_before_release(self, db, grabbing):
        w = _movie(db, release_date=d(30))
        assert movies.grab(db, w, force=True)["status"] == "started"

    def test_not_found(self, db, grabbing):
        w = _movie(db)
        grabbing["results"] = []
        assert movies.grab(db, w)["status"] == "not_found"
        assert movies.state(db, w) == "looking"

    def test_fallback_quality_only_after_48h(self, db, grabbing):
        from app.models import QualityProfile
        p = QualityProfile(name="4K", quality="2160p", codec="x265", fallback_quality="1080p", fallback_after_hours=24)
        db.add(p)
        db.commit()
        grabbing["results"] = []
        w = _movie(db, profile_id=p.id, release_date=d(-1))
        movies.grab(db, w)
        assert [q[1]["quality"] for q in grabbing["queries"]] == ["2160p"]
        grabbing["queries"].clear()
        w.release_date = d(-3)
        db.commit()
        movies.grab(db, w)
        assert [q[1]["quality"] for q in grabbing["queries"]] == ["2160p", "1080p"]

    def test_scanner_grabs_movies(self, Session, grabbing, monkeypatch):
        db = Session()
        _movie(db)
        _movie(db, title="Later", search_query="Later", tmdb_id=5, release_date=d(5))
        db.close()
        monkeypatch.setattr("app.database.SessionLocal", Session)
        from app.services import scheduler
        monkeypatch.setattr(scheduler, "_sync_download_statuses", lambda: None)
        monkeypatch.setattr(scheduler, "_cleanup_completed", lambda: None)
        scheduler.scan_watchlist()
        db = Session()
        assert [x.title for x in db.query(Download)] == [_result()["title"]]
        db.close()

    def test_search_now_route(self, api, db, grabbing):
        w = _movie(db, release_date=d(30))
        assert api.post(f"/api/watchlist/{w.id}/search-movie").json()["status"] == "started"
        tv = WatchlistItem(title="Show", search_query="Show")
        db.add(tv)
        db.commit()
        assert api.post(f"/api/watchlist/{tv.id}/search-movie").status_code == 422


# ── completion and removal ───────────────────────────────────────────────────


class TestFinishing:
    def test_completed_download_finishes_the_movie(self, db, grabbing):
        w = _movie(db)
        movies.grab(db, w)
        dl = db.query(Download).one()
        dl.status = "completed"
        db.commit()
        db.refresh(w)
        assert w.completed_at is not None and w.enabled is False
        assert movies.state(db, w) == "downloaded"

    def test_tv_downloads_do_not_finish_anything(self, db):
        tv = WatchlistItem(title="Show", search_query="Show")
        db.add(tv)
        db.commit()
        db.add(Download(title="Show S01E01", status="downloading", watchlist_id=tv.id))
        db.commit()
        db.query(Download).one().status = "completed"
        db.commit()
        db.refresh(tv)
        assert tv.completed_at is None and tv.enabled is True

    def test_cleanup_after_30_days(self, db):
        now = datetime.now(timezone.utc)
        old = _movie(db, title="Old", tmdb_id=1, completed_at=now - timedelta(days=31))
        kept = _movie(db, title="Kept", tmdb_id=2, completed_at=now - timedelta(days=60), keep=True)
        recent = _movie(db, title="Recent", tmdb_id=3, completed_at=now - timedelta(days=29))
        tv = WatchlistItem(title="Ended show", search_query="x", completed_at=now - timedelta(days=90))
        db.add(tv)
        db.commit()
        assert movies.removes_on(db, recent).date() == (now + timedelta(days=1)).date()
        assert movies.cleanup_finished(db) == ["Old"]
        left = {w.title for w in db.query(WatchlistItem)}
        assert left == {"Kept", "Recent", "Ended show"}
        assert kept.id and old.title

    def test_keep_days_setting(self, db):
        now = datetime.now(timezone.utc)
        _movie(db, completed_at=now - timedelta(days=8))
        db.add(Setting(key="watchlist_movie_keep_days", value="7"))
        db.commit()
        assert movies.cleanup_finished(db) == ["Dune Part Three"]
        _movie(db, tmdb_id=4, completed_at=now - timedelta(days=800))
        db.query(Setting).filter_by(key="watchlist_movie_keep_days").one().value = "0"
        db.commit()
        assert movies.cleanup_finished(db) == []

    def test_keep_toggle_via_update(self, api, db):
        w = _movie(db, completed_at=datetime.now(timezone.utc))
        r = api.put(f"/api/watchlist/{w.id}", json={"keep": True})
        assert r.json()["keep"] is True
        assert api.get("/api/watchlist").json()[0]["removes_at"] is None


# ── migration ────────────────────────────────────────────────────────────────


def test_backfill_marks_tmdb_matched_items_as_movies(tmp_path):
    from app.migrations import run_migrations
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.connect() as c:
        c.execute(text("CREATE TABLE watchlist (id INTEGER PRIMARY KEY, title TEXT NOT NULL, search_query TEXT NOT NULL, "
                       "tvmaze_id INTEGER, poster_url TEXT, premiered TEXT)"))
        c.execute(text("INSERT INTO watchlist VALUES (1, 'Dune', 'Dune', NULL, "
                       "'https://image.tmdb.org/t/p/w342/x.jpg', '2021-10-22')"))
        c.execute(text("INSERT INTO watchlist VALUES (2, 'Severance', 'Severance', 5, "
                       "'https://static.tvmaze.com/1.jpg', '2022-02-18')"))
        c.commit()
    run_migrations(engine)
    with engine.connect() as c:
        rows = c.execute(text("SELECT id, kind, year FROM watchlist ORDER BY id")).all()
    assert rows == [(1, "movie", 2021), (2, "tv", None)]
