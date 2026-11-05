"""A release only counts for a show when the show's name is the release's show part
(before the episode/season tag), not just a word somewhere in the title."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.candidates import find_candidates, matches_movie, matches_show, release_show_part


@pytest.mark.parametrize("title,names,ok", [
    ("MobLand S01E01 Stick or Twist 1080p HEVC x265-MeGusta", ["stick"], False),     # the reported case
    ("Stick.2025.S01E01.1080p.WEB.h264-GRP", ["Stick"], True),
    ("[EZTV] Stick S01E01 720p", ["stick"], True),
    ("Stick S01E01", ["Stick (2025)"], True),
    ("The.Office.US.S02E01.720p", ["The Office"], True),
    ("Doctor.Who.2005.S01E01.Rose", ["Doctor Who"], True),
    ("Bad.Sisters.S02E01.1080p", ["Sisters"], False),
    ("Law.and.Order.S01E01", ["Law & Order"], True),
    ("Mr Robot S01E01", ["Mr. Robot"], True),
    ("Greys.Anatomy.S20E01", ["Grey's Anatomy"], True),
    ("Severance.S02E05E06.2160p", ["Severance"], True),
    ("Severance 1x05", ["Severance"], True),
    ("Stick S01 COMPLETE 1080p", ["Stick"], True),                       # season pack
    ("MobLand S01 Stick 1080p", ["Stick"], False),
    ("Stick 1080p WEB", ["Stick"], False),                               # no episode/season tag
])
def test_matches_show(title, names, ok):
    assert matches_show(title, names) is ok


def test_either_name_counts():
    assert matches_show("Doctor Who S01E01", ["doctor who 2005 extra", "Doctor Who (2005)"])


def test_show_part():
    assert release_show_part("MobLand S01E01 Stick or Twist") == "mobland"
    assert release_show_part("No tag here") is None


@pytest.mark.parametrize("title,names,year,ok", [
    ("Dune.Part.Two.2024.1080p.WEB-DL", ["Dune Part Two"], 2024, True),
    ("Dune: Part Two (2024) [1080p]", ["Dune: Part Two"], 2024, True),
    ("Dune.2021.1080p", ["Dune Part Two"], 2024, False),
    ("Gone.Girl.2014.1080p", ["Gone"], 2014, False),
    ("Gone.2012.720p", ["Gone"], 2012, True),
    ("2001.A.Space.Odyssey.1968.1080p", ["2001 A Space Odyssey"], 1968, True),
])
def test_matches_movie(title, names, year, ok):
    assert matches_movie(title, names, year) is ok


def _r(title, seeds=50):
    return {"title": title, "seeds": seeds, "size_bytes": 1, "magnet": "m", "info_hash": title[:8]}


def test_find_candidates_drops_other_shows():
    item = SimpleNamespace(search_query="stick", title="Stick", quality="1080p", codec="x265",
                           max_size_mb=None, preferred_groups=None)
    results = [_r("MobLand S01E01 Stick or Twist 1080p HEVC x265-MeGusta", 500), _r("Stick S01E01 1080p x265", 10)]
    with patch("app.services.indexers.search_all", return_value=results):
        out = find_candidates(item, 1, 1, 1)
    assert [r["title"] for r in out] == ["Stick S01E01 1080p x265"]
