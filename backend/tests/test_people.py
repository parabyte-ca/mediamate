"""People: one shared watchlist, a view per person (you and T). Shows are downloaded
once; people follow them. Each device remembers who it belongs to (X-Person)."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.models import Person, PushSubscription, WatchlistFollower, WatchlistItem
from app.services import people, webpush

from tests.test_push import browser_subscription
from tests.test_watchlist_movies import Session, db  # noqa: F401


@pytest.fixture
def api(db):
    from app.routers import calendar, people as people_router, push, watchlist
    app = FastAPI()
    for r in (watchlist.router, people_router.router, calendar.router):
        app.include_router(r, prefix="/api")
    app.include_router(push.router, prefix="/api")

    def _override():
        yield db
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _show(db, title, tvmaze_id=None):
    w = WatchlistItem(title=title, search_query=title, season=1, episode=1, tvmaze_id=tvmaze_id)
    db.add(w)
    db.commit()
    return w


def _two(api):
    me = api.get("/api/people").json()[0]
    t = api.post("/api/people", json={"name": "T"}).json()
    return me, t


def test_the_owner_is_created_and_adopts_everything(db, api):
    a, b = _show(db, "Severance"), _show(db, "Slow Horses")
    got = api.get("/api/people").json()
    assert [p["name"] for p in got] == ["Me"] and got[0]["is_owner"]
    assert {f.watchlist_id for f in db.query(WatchlistFollower)} == {a.id, b.id}


def test_mine_and_everyone(db, api):
    _show(db, "Severance")
    me, t = _two(api)
    hdr = {"X-Person": str(t["id"])}
    assert api.get("/api/watchlist", headers=hdr).json() == []                     # T follows nothing yet
    assert [w["title"] for w in api.get("/api/watchlist?who=all", headers=hdr).json()] == ["Severance"]
    mine = api.get("/api/watchlist", headers={"X-Person": str(me["id"])}).json()
    assert mine[0]["followers"] == [me["id"]]
    assert len(api.get("/api/watchlist").json()) == 1                               # no person: everything


def test_adding_a_show_someone_else_has_just_follows_it(db, api):
    me, t = _two(api)
    sev = _show(db, "Severance", tvmaze_id=44933)
    body = {"title": "Severance", "search_query": "Severance", "tvmaze_id": 44933}
    r = api.post("/api/watchlist", json=body, headers={"X-Person": str(t["id"])})
    assert r.status_code == 201 and r.json()["id"] == sev.id
    assert db.query(WatchlistItem).count() == 1                                     # still one show, one download
    assert sorted(people.followers(db)[sev.id]) == sorted([me["id"], t["id"]])
    assert api.post("/api/watchlist", json=body, headers={"X-Person": str(t["id"])}).status_code == 409


def test_a_new_show_is_followed_by_who_added_it(db, api):
    me, t = _two(api)
    r = api.post("/api/watchlist", json={"title": "Bluey", "search_query": "Bluey"}, headers={"X-Person": str(t["id"])})
    assert r.status_code == 201
    assert people.followers(db)[r.json()["id"]] == [t["id"]]


def test_removing_a_shared_show_only_unfollows(db, api):
    me, t = _two(api)
    sev = _show(db, "Severance")
    api.post(f"/api/watchlist/{sev.id}/follow", headers={"X-Person": str(t["id"])})
    r = api.delete(f"/api/watchlist/{sev.id}", headers={"X-Person": str(t["id"])})
    assert r.json() == {"ok": True, "unfollowed": True} and db.get(WatchlistItem, sev.id) is not None
    r = api.delete(f"/api/watchlist/{sev.id}", headers={"X-Person": str(me["id"])})
    assert "unfollowed" not in r.json()
    db.expire_all()
    assert db.get(WatchlistItem, sev.id) is None                                     # nobody else: gone


def test_unfollowing_the_last_follower_hands_it_to_the_owner(db, api):
    me, t = _two(api)
    w = api.post("/api/watchlist", json={"title": "Bluey", "search_query": "Bluey"},
                 headers={"X-Person": str(t["id"])}).json()
    out = api.post(f"/api/watchlist/{w['id']}/unfollow", headers={"X-Person": str(t["id"])}).json()
    assert out["followers"] == [me["id"]]


def test_removing_a_person(db, api):
    me, t = _two(api)
    w = api.post("/api/watchlist", json={"title": "Bluey", "search_query": "Bluey"},
                 headers={"X-Person": str(t["id"])}).json()
    assert api.delete(f"/api/people/{me['id']}").status_code == 400                 # never the owner
    assert api.delete(f"/api/people/{t['id']}").json() == {"ok": True}
    assert people.followers(db)[w["id"]] == [me["id"]]
    assert api.patch(f"/api/people/{me['id']}", json={"name": "Alan"}).json()["name"] == "Alan"
    assert api.post("/api/people", json={"name": " "}).status_code == 400
    assert api.post("/api/people", json={"name": "alan"}).status_code == 409


def test_calendar_shows_only_mine(db, api):
    me, t = _two(api)
    _show(db, "Severance", tvmaze_id=1)
    seen = {}

    async def fake(db_, days_back, days_ahead, only=None):
        seen["only"] = only
        return {"entries": []}
    with patch("app.routers.calendar.build_calendar", side_effect=fake):
        api.get("/api/calendar", headers={"X-Person": str(t["id"])})
        assert seen["only"] == set()
        api.get("/api/calendar?who=all", headers={"X-Person": str(t["id"])})
        assert seen["only"] is None


def test_alerts_go_to_the_followers_devices(Session, monkeypatch):
    monkeypatch.setattr(webpush, "_session_factory", Session)
    with Session() as s:
        me = people.ensure_owner(s)
        t = Person(name="T")
        s.add(t)
        s.commit()
        mine = _show(s, "Severance")
        theirs = _show(s, "Bluey")
        people.ensure_owner(s)                                   # Severance and Bluey: the owner's
        people.unfollow(s, theirs.id, me.id)
        people.follow(s, theirs.id, t.id)
        webpush.subscribe(s, browser_subscription("https://push.example.com/android"), "Android", me.id)
        webpush.subscribe(s, browser_subscription("https://push.example.com/iphone"), "iPhone", t.id)
        webpush.subscribe(s, browser_subscription("https://push.example.com/laptop"), "Laptop")   # nobody's
        ids = (mine.id, theirs.id)

    def endpoints(**kw):
        with patch("requests.post", return_value=MagicMock(status_code=201, text="")) as post:
            webpush.send_all("T", "B", **kw)
        return sorted(c.args[0].rsplit("/", 1)[-1] for c in post.call_args_list)
    assert endpoints(watchlist_id=ids[0]) == ["android", "laptop"]
    assert endpoints(watchlist_id=ids[1]) == ["iphone", "laptop"]
    assert endpoints(system=True) == ["android", "laptop"]                    # system alerts: the owner
    assert endpoints() == ["android", "iphone", "laptop"]                     # a test push: everyone


def test_a_device_changes_hands(db, api):
    me, t = _two(api)
    sub = browser_subscription("https://push.example.com/phone")
    api.post("/api/push/subscribe", json={"subscription": sub, "label": "Phone", "person_id": me["id"]})
    assert db.query(PushSubscription).one().person_id == me["id"]
    api.post("/api/push/person", json={"endpoint": sub["endpoint"], "person_id": t["id"]})
    db.expire_all()
    assert db.query(PushSubscription).one().person_id == t["id"]
