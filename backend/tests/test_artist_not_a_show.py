"""A music artist ("Sloan", "Beck") is never treated as a TV show of the same name: the
show-status check skips it, the calendar and digest ignore it, and a one-time repair
clears matches (and pauses) left by older versions."""
import asyncio
from datetime import datetime, timedelta, timezone

from app.models import WatchlistItem
from app.services import calendar_view, episode_guide, music, scheduler

from tests.test_watchlist_movies import Session, db  # noqa: F401


def _items(db):  # noqa: F811
    artist = WatchlistItem(title="Sloan", search_query="Sloan", kind="artist", mbid="a-sloan", enabled=True)
    show = WatchlistItem(title="Stick", search_query="Stick", kind="tv", tvmaze_id=500, enabled=True)
    db.add_all([artist, show])
    db.commit()
    return artist, show


def test_status_check_skips_artists(db, monkeypatch):  # noqa: F811
    artist, show = _items(db)
    looked_up = []

    async def fake_status(title, tvmaze_id):
        looked_up.append(title)
        return "Ended", 999
    monkeypatch.setattr("app.services.tvmaze.get_show_status", fake_status)
    asyncio.run(scheduler._check_statuses_async(db))
    db.refresh(artist)
    assert looked_up == ["Stick"]
    assert artist.enabled and artist.tvmaze_id is None and artist.show_status is None


def test_calendar_ignores_artists_even_with_a_stray_match(db, monkeypatch):  # noqa: F811
    artist, show = _items(db)
    artist.tvmaze_id = 777                       # left over from the old bug
    db.commit()
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    fetched = []

    async def fake_eps(tvmaze_id):
        fetched.append(tvmaze_id)
        return [{"season": 1, "number": 3, "name": "Ep", "airs_at": soon}]
    monkeypatch.setattr(episode_guide, "aget_episodes", fake_eps)
    cal = asyncio.run(calendar_view.build_calendar(db, days_back=0, days_ahead=7))
    assert fetched == [500] and [e["title"] for e in cal["entries"]] == ["Stick"]


def test_repair_clears_matches_and_unpauses_artists(db):  # noqa: F811
    paused = WatchlistItem(title="Beck", search_query="Beck", kind="artist", mbid="a-beck", enabled=False,
                           tvmaze_id=123, show_status="Ended")
    chosen = WatchlistItem(title="Quiet One", search_query="Quiet One", kind="artist", mbid="a-q", enabled=False)
    show = WatchlistItem(title="Gone", search_query="Gone", kind="tv", tvmaze_id=9, show_status="Ended", enabled=False)
    db.add_all([paused, chosen, show])
    db.commit()
    assert music.repair_artist_show_matches(db) == 1
    for x in (paused, chosen, show):
        db.refresh(x)
    assert paused.enabled and paused.tvmaze_id is None and paused.show_status is None
    assert not chosen.enabled                     # paused by you: left alone
    assert show.tvmaze_id == 9 and not show.enabled   # real shows untouched
