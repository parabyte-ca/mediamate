"""The weekly Plex digest: sorting into buckets, rendering and sending (Plex and SMTP mocked)."""
from email import message_from_string
from email.header import decode_header, make_header
from unittest.mock import MagicMock, patch

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
    monkeypatch.setattr(pd, "_fetch_thumb_b64", lambda u, t, p: "data:image/jpeg;base64,AAAA")
    monkeypatch.setattr(pd, "_fetch_show_summary", lambda u, t, k: "A show.")
    return pd.fetch_digest_sections("http://plex", "tok", excluded_libs=excluded)


def test_items_are_sorted_into_family_and_mature_buckets(monkeypatch):
    s = _sections(monkeypatch)
    assert [m["title"] for m in s["movies"]] == ["Family Film"]
    assert [m["title"] for m in s["mature_movies"]] == ["Late Film"]
    [kids] = s["tv"]
    assert kids["title"] == "Kids Show" and kids["episodes"] == ["S01E01", "S01E02"]
    assert [t["title"] for t in s["mature_tv"]] == ["Grown Show"]
    assert kids["plex_link"].endswith("key=%2Flibrary%2Fmetadata%2F77") and "MID" in kids["plex_link"]


def test_the_email_lists_every_title_and_escapes_html(monkeypatch):
    s = _sections(monkeypatch)
    s["movies"][0]["title"] = "Tom & <Jerry>"
    html = pd.render_html(s, "Week of Oct 27")
    assert "Tom &amp; &lt;Jerry&gt;" in html and "<Jerry>" not in html
    for title in ("Late Film", "Kids Show", "Grown Show", "Week of Oct 27"):
        assert title in html


def test_sending_uses_starttls_and_logs_in(monkeypatch):
    server = MagicMock()
    with patch("smtplib.SMTP") as smtp:
        smtp.return_value.__enter__.return_value = server
        pd.send_email({"host": "mail", "port": "587", "user": "u", "password": "p", "from_email": "f@x"},
                      ["a@x", "b@x"], "<p>hi</p>", "Week 1")
    smtp.assert_called_once_with("mail", 587, timeout=15)
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("u", "p")
    sender, to, body = server.sendmail.call_args.args
    assert (sender, to) == ("f@x", ["a@x", "b@x"])
    subject = str(make_header(decode_header(message_from_string(body)["Subject"])))
    assert subject == "MediaMate Weekly Digest — Week 1"


def test_port_465_uses_implicit_tls(monkeypatch):
    with patch("smtplib.SMTP_SSL") as ssl_smtp, patch("smtplib.SMTP") as plain:
        pd.send_email({"host": "mail", "port": "465"}, ["a@x"], "<p/>", "W")
    ssl_smtp.assert_called_once()
    plain.assert_not_called()
