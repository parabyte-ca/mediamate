"""The weekly Plex digest: sorting into buckets, rendering and sending (Plex and SMTP mocked)."""
from email import message_from_string
from email.header import decode_header, make_header
from unittest.mock import MagicMock, patch

import pytest

from app.services import plex_digest as pd


def _movie(title, rating):
    return {"title": title, "content_rating": rating, "thumb": "/t", "rating_key": "11",
            "summary": "A film.", "year": "2024", "added_at": 1}


def _episode(show, rating, s, e):
    return {"title": f"Ep {e}", "grandparent_title": show, "content_rating": rating,
            "grandparent_key": "/library/metadata/77", "grandparent_thumb": "/gt",
            "season_num": str(s), "episode_num": str(e), "added_at": 1}


def _sections(monkeypatch, excluded=frozenset()):
    libs = {"movie": ["1"], "show": ["2"]}
    recent = {"1": [_movie("Family Film", "PG"), _movie("Late Film", "R")],
              "2": [_episode("Kids Show", "TV-Y", 1, 1), _episode("Kids Show", "TV-Y", 1, 2),
                    _episode("Grown Show", "TV-MA", 2, 5)]}
    monkeypatch.setattr(pd, "_fetch_machine_identifier", lambda u, t: "MID")
    monkeypatch.setattr(pd, "_get_library_ids_by_type", lambda u, t, kind, ex: libs[kind])
    monkeypatch.setattr(pd, "_fetch_recently_added", lambda u, t, lib, days: [dict(x) for x in recent[lib]])
    monkeypatch.setattr(pd, "_fetch_image", lambda u, t, p, w=200, h=300: b"\xff\xd8JPEG" + str(w).encode())
    monkeypatch.setattr(pd, "_fetch_show_summary", lambda u, t, k: "A show.")
    return pd.fetch_digest_sections("http://plex", "tok", excluded_libs=excluded)


def test_items_are_sorted_into_family_and_mature_buckets(monkeypatch):
    s = _sections(monkeypatch)
    assert [m["title"] for m in s["movies"]] == ["Family Film"]
    assert [m["title"] for m in s["mature_movies"]] == ["Late Film"]
    [kids] = s["tv"]
    assert kids["title"] == "Kids Show" and kids["episodes"] == ["S01E01", "S01E02"]
    assert kids["episode_ranges"] == "S01E01–02" and kids["image"].startswith(b"\xff\xd8")
    assert [t["title"] for t in s["mature_tv"]] == ["Grown Show"]
    assert kids["plex_link"].endswith("key=%2Flibrary%2Fmetadata%2F77") and "MID" in kids["plex_link"]


def _digest(monkeypatch, music=None, coming=None):
    secs = _sections(monkeypatch)
    secs["movies"][0]["rating"] = "8.4"
    return {"sections": secs, "highlight": pd.pick_feature(secs), "music": music or [],
            "coming": coming or {"shows": [], "movies": []}}


def test_the_email_lists_every_title_and_escapes_html(monkeypatch):
    d = _digest(monkeypatch)
    d["sections"]["mature_movies"][0]["title"] = "Tom & <Jerry>"
    html, images = pd.render_html(d, "Week of Oct 27")
    assert "Tom &amp; &lt;Jerry&gt;" in html and "<Jerry>" not in html
    for title in ("Family Film", "Kids Show", "Grown Show", "Week of Oct 27", "This week from MediaMate"):
        assert title in html


def test_feature_is_the_mature_movie_not_the_family_film(monkeypatch):
    d = _digest(monkeypatch)                              # Family Film is rated higher
    assert d["highlight"]["item"]["title"] == "Late Film"
    html, _ = pd.render_html(d, "W")
    assert html.count("Feature of the week") == 1 and "Watch in Plex" in html
    assert "highlight" not in html.lower() and "Mature pick" not in html
    assert html.count(">Late Film<") == 1                 # not repeated in the grid
    assert ">Family Film<" in html                        # still listed in its section
    assert "Feature of the week: Late Film" in pd.render_text(d, "W")


def test_posters_are_attached_not_data_uris(monkeypatch):
    d = _digest(monkeypatch)
    html, images = pd.render_html(d, "W")
    assert "data:image" not in html and 'src="cid:' in html and images
    assert all(f"cid:{cid}" in html for cid in images)
    inline, none = pd.render_html(d, "W", inline_images=True)
    assert "data:image/jpeg;base64," in inline and not none


def test_tv_is_grouped_with_episode_ranges():
    assert pd.episode_ranges(["S01E04", "S01E05", "S01E06", "S02E01", "S01E09"]) == "S01E04–06, S01E09, S02E01"
    assert pd.episode_ranges(["S03E01"]) == "S03E01" and pd.episode_ranges([]) == ""


def test_music_and_coming_next_week_sections(monkeypatch):
    music = [{"title": "Souvlaki", "artist": "Slowdive", "year": 1993, "format": "FLAC", "tracks": 10, "image": b"\xff\xd8x"}]
    coming = {"shows": [{"title": "Stick", "poster_url": None,
                         "episodes": [{"code": "S01E07", "name": "Pin High", "airs_at": "2026-12-03T01:00:00+00:00"},
                                      {"code": "S01E08", "name": "", "airs_at": "2026-12-04T01:00:00+00:00"}]}],
              "movies": [{"title": "Dune Part Three", "year": 2026, "release_date": "2026-12-01", "poster_url": None}]}
    d = _digest(monkeypatch, music, coming)
    html, _ = pd.render_html(d, "W")
    for bit in ("New music", "Souvlaki", "Slowdive", "Coming next week", "Stick", "S01E07", "+ 1 more", "Dune Part Three"):
        assert bit in html
    text = pd.render_text(d, "W", "https://mm.example/api/public/digest/abc")
    assert "Slowdive – Souvlaki" in text and "Stick S01E07" in text and "View in browser: https://mm.example" in text


def test_a_quiet_week_says_so(monkeypatch):
    d = {"sections": {"movies": [], "mature_movies": [], "tv": [], "mature_tv": []}, "highlight": None,
         "music": [], "coming": {"shows": [], "movies": []}}
    html, images = pd.render_html(d, "W")
    assert "Nothing new was added this week." in html and not images
    assert "Nothing new was added this week." in pd.render_text(d, "W")


def test_view_in_browser_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "_view_dir", lambda: tmp_path / "digest")
    t1 = pd.save_view_copy("<p>one</p>")
    assert pd.view_copy(t1) == "<p>one</p>"
    t2 = pd.save_view_copy("<p>two</p>")
    assert pd.view_copy(t1) is None and pd.view_copy(t2) == "<p>two</p>"   # only the newest is kept
    assert pd.view_copy("../../etc/passwd") is None


def test_sending_uses_starttls_and_logs_in(monkeypatch):
    server = MagicMock()
    with patch("smtplib.SMTP") as smtp:
        smtp.return_value.__enter__.return_value = server
        pd.send_email({"host": "mail", "port": "587", "user": "u", "password": "p", "from_email": "f@x"},
                      ["a@x", "b@x"], "<p>hi</p>", "Week 1", text="hi", images={"p-0@mediamate": b"\xff\xd8x"})
    smtp.assert_called_once_with("mail", 587, timeout=30)
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("u", "p")
    assert server.sendmail.call_count == 2                 # one copy each
    (s1, to1, _), (s2, to2, body) = [c.args for c in server.sendmail.call_args_list]
    assert (s1, to1, s2, to2) == ("f@x", ["a@x"], "f@x", ["b@x"])
    msg = message_from_string(body)
    assert msg["To"] == "b@x" and "a@x" not in body          # nobody sees the others
    assert msg["Message-ID"]
    subject = str(make_header(decode_header(msg["Subject"])))
    assert subject == "This week from MediaMate — Week 1"
    kinds = [p.get_content_type() for p in msg.walk()]
    assert kinds[:3] == ["multipart/alternative", "text/plain", "multipart/related"]
    assert "text/html" in kinds and "image/jpeg" in kinds
    img = next(p for p in msg.walk() if p.get_content_type() == "image/jpeg")
    assert img["Content-ID"] == "<p-0@mediamate>"


def test_port_465_uses_implicit_tls(monkeypatch):
    with patch("smtplib.SMTP_SSL") as ssl_smtp, patch("smtplib.SMTP") as plain:
        pd.send_email({"host": "mail", "port": "465"}, ["a@x"], "<p/>", "W")
    ssl_smtp.assert_called_once()
    plain.assert_not_called()


def test_preview_and_view_routes(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(pd, "_view_dir", lambda: tmp_path / "digest")
    token = pd.save_view_copy("<html>digest</html>")
    c = TestClient(app)
    r = c.get(f"/api/public/digest/{token}")
    assert r.status_code == 200 and "digest" in r.text and r.headers["content-type"].startswith("text/html")
    assert c.get("/api/public/digest/notarealtoken_notarealtoken").status_code == 404


def test_feature_falls_back_to_the_best_new_title_without_mature_movies():
    old = {"title": "Casablanca", "year": "1942", "rating": "9.9", "added_at": 5}
    new = {"title": "New Film", "year": "2026", "rating": "7.0", "added_at": 1}
    newer_better = {"title": "Better New", "year": "2025", "rating": "8.0", "added_at": 1}
    pick = lambda movies, tv=(): pd.pick_feature({"movies": list(movies), "tv": list(tv),
                                                  "mature_movies": [], "mature_tv": []}, this_year=2026)["item"]["title"]
    assert pick([old, new]) == "New Film"
    assert pick([old, new, newer_better]) == "Better New"            # among new releases, the best rated
    assert pick([{"title": "Older", "year": "2010", "rating": "9"}, {"title": "Newer", "year": "2019", "rating": "6"}]) == "Newer"
    show = {"title": "New Show", "release_year": 2026, "rating": "8.5", "episodes": ["S01E01"]}
    assert pick([old], [show]) == "New Show"
    assert pd.pick_feature({"movies": [], "tv": [], "mature_movies": [], "mature_tv": []}) is None


def test_unmatched_files_are_left_out():
    assert pd.is_vague({"title": "Some.Movie.2019.1080p.mkv"})
    assert pd.is_vague({"title": "clip_0042", "summary": ""})
    assert pd.is_vague({"title": "Mystery thing", "summary": "", "year": ""})
    assert pd.is_vague({"title": "_Unsorted"})
    assert not pd.is_vague({"title": "Dune", "year": "2021", "summary": ""})
    assert not pd.is_vague({"title": "Dune", "summary": "Spice."})
    assert pd.is_vague_show("_Unsorted", {}) and pd.is_vague_show("Show.Name.S01E02.720p", {})
    assert not pd.is_vague_show("The Simpsons", {})


def test_vague_items_never_reach_the_digest(monkeypatch):
    libs = {"movie": ["1"], "show": ["2"]}
    recent = {"1": [_movie("Good Film", "PG"), {"title": "VID_2031.mp4", "content_rating": "", "thumb": "", "summary": "",
                                                  "year": "", "rating_key": "9", "added_at": 1}],
              "2": [_episode("_Unsorted", "", 1, 1), _episode("Real Show", "TV-PG", 1, 1)]}
    monkeypatch.setattr(pd, "_fetch_machine_identifier", lambda u, t: "MID")
    monkeypatch.setattr(pd, "_get_library_ids_by_type", lambda u, t, kind, ex: libs[kind])
    monkeypatch.setattr(pd, "_fetch_recently_added", lambda u, t, lib, days: [dict(x) for x in recent[lib]])
    monkeypatch.setattr(pd, "_fetch_image", lambda *a, **k: None)
    monkeypatch.setattr(pd, "_fetch_show_summary", lambda u, t, k: "")
    s = pd.fetch_digest_sections("http://plex", "tok")
    titles = [i["title"] for k in s for i in s[k]]
    assert titles == ["Good Film", "Real Show"]


def test_recipients_are_deduplicated():
    assert pd.parse_recipients("A@x.com\nb@y.com, a@X.com; c@z.com  b@y.com") == ["A@x.com", "b@y.com", "c@z.com"]
    assert pd.parse_recipients("") == [] and pd.parse_recipients(None) == []


def test_one_refused_recipient_does_not_stop_the_rest():
    import smtplib
    server = MagicMock()
    def send(frm, to, body):
        if to == ["bad@x"]:
            raise smtplib.SMTPRecipientsRefused({"bad@x": (550, b"no such user")})
    server.sendmail.side_effect = send
    with patch("smtplib.SMTP") as smtp:
        smtp.return_value.__enter__.return_value = server
        r = pd.send_email({"host": "mail"}, ["a@x", "bad@x", "A@x", "c@x"], "<p/>", "W")
    assert r["sent"] == ["a@x", "c@x"] and list(r["failed"]) == ["bad@x"]
    assert server.sendmail.call_count == 3                 # duplicate "A@x" sent once


def test_all_refused_raises():
    import smtplib
    server = MagicMock()
    server.sendmail.side_effect = smtplib.SMTPRecipientsRefused({"x": (550, b"no")})
    with patch("smtplib.SMTP") as smtp:
        smtp.return_value.__enter__.return_value = server
        with pytest.raises(RuntimeError):
            pd.send_email({"host": "mail"}, ["a@x"], "<p/>", "W")


@pytest.fixture
def digest_db(tmp_path, monkeypatch):
    """A throwaway database used by the app, the scheduler and the routes."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import models  # noqa: F401
    from app.database import Base, get_db
    from app.main import app
    engine = create_engine(f"sqlite:///{tmp_path / 'd.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("app.database.SessionLocal", Session)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = _db
    yield Session
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def _digest_settings(db, **extra):
    from app.models import Setting
    vals = {"digest_enabled": "true", "digest_recipients": "a@x\nb@x", "plex_url": "http://plex",
            "plex_token": "tok", "digest_smtp_host": "mail", **extra}
    for k, v in vals.items():
        row = db.query(Setting).filter(Setting.key == k).first()
        if row:
            row.value = v
        else:
            db.add(Setting(key=k, value=v))
    db.commit()


def test_send_now_records_history_and_guards_repeat(monkeypatch, digest_db):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models import Setting
    from app.services import scheduler
    db = digest_db()
    _digest_settings(db)
    db.close()
    monkeypatch.setattr(pd, "compose", lambda *a, **k: {"html": "<p/>", "text": "t", "images": {}})
    sent = []
    monkeypatch.setattr(pd, "send_email", lambda cfg, rcpt, *a, **k: sent.append(list(rcpt)) or {"sent": list(rcpt), "failed": {}})
    c = TestClient(app)
    r = c.post("/api/settings/send-digest-now", json={}).json()
    assert r["ok"] and r["sent"] == 2 and sent == [["a@x", "b@x"]]
    r = c.post("/api/settings/send-digest-now", json={}).json()   # within 12 h
    assert not r["ok"] and r["code"] == "recently_sent" and len(sent) == 1
    scheduler.run_weekly_digest()                                   # the schedule is guarded too
    assert len(sent) == 1
    r = c.post("/api/settings/send-digest-now", json={"force": True}).json()
    assert r["ok"] and len(sent) == 2
    h = c.get("/api/settings/digest-history").json()
    assert [x["kind"] for x in h[:2]] == ["manual", "manual"] and h[0]["sent"] == 2
    assert "digest_history" not in c.get("/api/settings").json()


def test_failed_send_is_recorded(monkeypatch, digest_db):
    from app.models import Setting
    from app.services import scheduler
    db = digest_db()
    _digest_settings(db, digest_smtp_password="hunter2secret")
    db.close()
    monkeypatch.setattr(pd, "compose", lambda *a, **k: {"html": "<p/>", "text": "t", "images": {}})
    def boom(*a, **k):
        raise OSError("login failed for hunter2secret")
    monkeypatch.setattr(pd, "send_email", boom)
    r = scheduler.send_digest("scheduled")
    assert not r["ok"] and "hunter2secret" not in r["error"]
    db = digest_db()
    h = pd.history(db)
    db.close()
    assert h[0]["kind"] == "scheduled" and h[0]["error"] and "hunter2secret" not in h[0]["error"]


def test_feature_is_the_newest_most_popular_mature_movie():
    m = lambda t, y, aud, added=0: {"title": t, "year": str(y), "audience_rating": str(aud),
                                    "rating": "", "added_at": added}
    family = {"title": "Family Hit", "year": "2026", "rating": "9.9", "added_at": 9}
    pick = lambda movies: pd.pick_feature({"movies": [family], "mature_movies": list(movies),
                                           "tv": [], "mature_tv": []}, this_year=2026)["item"]["title"]
    old_hit, new_ok, new_hit = m("Old Classic", 1994, 9.6), m("New OK", 2026, 6.1), m("New Hit", 2025, 8.2)
    assert pick([old_hit, new_ok, new_hit]) == "New Hit"           # recent first, then popular; never the family film
    assert pick([old_hit, m("Older", 1990, 9.9)]) == "Old Classic"  # none recent: newest
