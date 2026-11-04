"""Rename & organise: name parsing, targets, moving (with subtitles), the pipeline
step, the per-file and bulk endpoints, and the settings warnings."""
import os

import pytest

from app.models import Download, PipelinePlan, Setting, WatchlistItem
from app.services import organise, pipeline
from app.services.media import config, store

# Reuse the pipeline tests' environment and helpers.
from tests.test_pipeline import HAS_FFMPEG, _clip, _complete, _download, _rule, env  # noqa: F401

S = dict(organise.SETTINGS_DEFAULTS, organise_tv_dir="/tv", organise_movie_dir="/movies")


@pytest.mark.parametrize("name,target", [
    ("The.Bear.S03E01.1080p.WEB.h264-GROUP.mkv", "/tv/The Bear/Season 03/The Bear - S03E01.mkv"),
    ("severance.s02e05e06.2160p.mkv", "/tv/Severance/Season 02/Severance - S02E05-E06.mkv"),
    ("show_name_1x05_hdtv.avi", "/tv/Show Name/Season 01/Show Name - S01E05.avi"),
    ("Doctor Who 2005 S01E01 Rose.mkv", "/tv/Doctor Who/Season 01/Doctor Who - S01E01.mkv"),
    ("Dune.Part.Two.2024.2160p.WEB-DL.mkv", "/movies/Dune Part Two (2024)/Dune Part Two (2024).mkv"),
    ("2001.A.Space.Odyssey.1968.1080p.BluRay.mkv",
     "/movies/2001 A Space Odyssey (1968)/2001 A Space Odyssey (1968).mkv"),
    ("Blade Runner 2049 (2017) 1080p.mkv", "/movies/Blade Runner 2049 (2017)/Blade Runner 2049 (2017).mkv"),
    ("Movie.x264.2019.MKV", "/movies/Movie (2019)/Movie (2019).MKV"),   # extension kept exactly
])
def test_targets(name, target):
    assert organise.target_for(S, "/dl/" + name)[0] == target


def test_the_watchlist_name_wins_and_bad_characters_go():
    dest, _ = organise.target_for(S, "/dl/mr.robot.s01e01.mkv", show_hint="Mr. Robot: Who?")
    assert dest == "/tv/Mr. Robot Who/Season 01/Mr. Robot Who - S01E01.mkv"


@pytest.mark.parametrize("settings,name,msg", [
    (dict(S, organise_tv_dir=""), "Show.S01E01.mkv", "Set a TV folder"),
    (dict(S, organise_movie_dir=""), "Film.2020.mkv", "Set a Movies folder"),
    (S, "[Grp] Some Anime - 05 [1080p].mkv", "episode (S01E02) or a movie"),
])
def test_unclear_cases_say_why(settings, name, msg):
    with pytest.raises(organise.OrganiseError, match=msg.replace("(", r"\(").replace(")", r"\)")):
        organise.target_for(settings, "/dl/" + name)


def test_custom_templates():
    s = dict(S, organise_tv_template="{show}/S{season}/{episode:03d}{ext}")
    assert organise.target_for(s, "/dl/Show.S02E07.mkv")[0] == "/tv/Show/S2/007.mkv"


def test_move_takes_subtitles_and_clears_leftovers(tmp_path):
    src_dir = tmp_path / "dl" / "Show.S01E01.1080p"
    src_dir.mkdir(parents=True)
    video = src_dir / "Show.S01E01.1080p.mkv"
    video.write_bytes(b"v")
    (src_dir / "Show.S01E01.1080p.en.srt").write_text("1")
    (src_dir / "release.nfo").write_text("x")
    dest = tmp_path / "tv" / "Show" / "Season 01" / "Show - S01E01.mkv"

    moved = organise.move(str(video), str(dest))
    assert dest.read_bytes() == b"v"
    assert (dest.parent / "Show - S01E01.en.srt").exists() and len(moved) == 2
    assert not src_dir.exists()                       # only junk was left behind


def test_move_never_overwrites(tmp_path):
    a, b = tmp_path / "a.mkv", tmp_path / "b.mkv"
    a.write_bytes(b"1")
    b.write_bytes(b"2")
    with pytest.raises(organise.OrganiseError, match="already exists"):
        organise.move(str(a), str(b))
    assert a.read_bytes() == b"1" and b.read_bytes() == b"2"


def test_validate_places_organise_last_and_not_with_split():
    assert pipeline.validate({}, [{"type": "encode"}, {"type": "organise"}]) is None
    assert "last" in pipeline.validate({}, [{"type": "organise"}, {"type": "encode"}])
    assert "same rule" in pipeline.validate({}, [{"type": "organise"}, {"type": "split"}])


def _settings(Session, **kv):
    with Session() as db:
        for k, v in kv.items():
            db.merge(Setting(key=k, value=v))
        db.commit()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_pipeline_organises_a_download_using_the_watchlist_name(env):
    engine, Session, tmp = env
    tv = tmp / "tv"
    tv.mkdir()
    _settings(Session, organise_tv_dir=str(tv))
    _rule(Session, name="File it", match={}, actions=[{"type": "organise"}])
    with Session() as db:
        w = WatchlistItem(title="The Bear", search_query="the bear")
        db.add(w)
        db.commit()
        wid = w.id
    dl_id, dl_dir = _download(Session, tmp, name="the.bear.s03e01.mkv", watchlist_id=wid)
    _clip(dl_dir / "the.bear.s03e01.mkv")
    size = (dl_dir / "the.bear.s03e01.mkv").stat().st_size

    rm = _complete(Session, dl_id, content="/remote/dl/the.bear.s03e01.mkv")
    rm.assert_called_once_with("abc", delete_files=False)   # stop seeding, keep the file

    dest = tv / "The Bear" / "Season 03" / "The Bear - S03E01.mkv"
    assert dest.stat().st_size == size and not (dl_dir / "the.bear.s03e01.mkv").exists()
    with Session() as db:
        plan = db.query(PipelinePlan).one()
        assert (plan.status, plan.organise_status, plan.organised_to) == ("done", "done", str(dest))
        row = store.get_file(plan.file_id)
        assert row["path"] == str(dest) and row["filename"] == dest.name
        steps = pipeline.plan_steps(db, plan.journey_id)
    assert steps[0]["kind"] == "organise" and steps[0]["status"] == "done"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_pipeline_reports_why_it_could_not_organise(env):
    engine, Session, tmp = env
    _rule(Session, name="File it", match={}, actions=[{"type": "organise"}])
    dl_id, dl_dir = _download(Session, tmp, name="Show.S01E01.mkv")
    _clip(dl_dir / "Show.S01E01.mkv")
    _complete(Session, dl_id)
    with Session() as db:
        plan = db.query(PipelinePlan).one()
        assert plan.status == "failed" and "Set a TV folder" in plan.detail
    assert (dl_dir / "Show.S01E01.mkv").exists()                 # nothing moved


@pytest.fixture
def lib(env, monkeypatch):
    engine, Session, tmp = env
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.main import app
    import app.database as database
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    media, movies = tmp / "media", tmp / "media" / "Movies"
    movies.mkdir(parents=True)
    monkeypatch.setattr(config, "MEDIA_PATHS", [str(media)])
    _settings(Session, organise_movie_dir=str(movies))

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app), Session, media
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_library_preview_and_organise_one(lib):
    client, Session, media = lib
    f = media / "Dune.Part.Two.2024.2160p.mkv"
    f.write_bytes(b"x")
    fid = store.upsert_file(str(f), f.name, 1, 60.0, "hevc", False)
    r = client.get(f"/api/media/files/{fid}/organise").json()
    assert r["ok"] and r["target"].endswith("Movies/Dune Part Two (2024)/Dune Part Two (2024).mkv")
    r = client.post(f"/api/media/files/{fid}/organise").json()
    assert r["ok"] and os.path.exists(r["path"]) and not f.exists()


def test_bulk_organise_reports_each_problem(lib):
    client, Session, media = lib
    good, bad = media / "Film.2020.mkv", media / "random clip.mkv"
    for p in (good, bad):
        p.write_bytes(b"x")
    ids = [store.upsert_file(str(p), p.name, 1, 60.0, "h264", False) for p in (good, bad)]
    r = client.post("/api/media/files/bulk", json={"action": "organise", "file_ids": ids}).json()
    assert r["done"] == 1 and len(r["errors"]) == 1 and "random clip" in r["errors"][0]


def test_settings_warn_about_folders_the_library_would_lose(lib, tmp_path):
    client, Session, media = lib
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    r = client.put("/api/pipeline/organise", json={"organise_tv_dir": str(outside)}).json()
    assert "isn't inside a folder with Scan & encode on" in r["warnings"]["organise_tv_dir"]
    r = client.put("/api/pipeline/organise", json={"organise_tv_dir": "/nope/tv"}).json()
    assert "doesn't exist" in r["warnings"]["organise_tv_dir"]
    ex = client.post("/api/pipeline/organise/example", json={"filename": "Show.S01E02.mkv"}).json()
    assert ex["ok"] and ex["target"] == "/nope/tv/Show/Season 01/Show - S01E02.mkv"
    bad = client.post("/api/pipeline/organise/example",
                      json={"filename": "Show.S01E02.mkv", "organise_tv_template": "{nope}"}).json()
    assert not bad["ok"] and "template" in bad["error"]


# ── the extension never changes ──────────────────────────────────────────────


@pytest.mark.parametrize("name,ext", [
    ("Dune.2021.1080p.mp4", ".mp4"), ("Dune.2021.1080p.MKV", ".MKV"), ("Old.Film.1999.avi", ".avi"),
    ("Show.S01E01.m4v", ".m4v"), ("Show.S01E01E02.mp4", ".mp4"),
])
def test_rename_keeps_the_files_own_extension(name, ext):
    dest = organise.target_for(S, "/dl/" + name)[0]
    assert dest.endswith(ext) and os.path.splitext(dest)[1] == ext


@pytest.mark.parametrize("template", [
    "{title} ({year})/{title} ({year}).mkv",          # a hard-coded .mkv
    "{title} ({year})/{title} ({year})",             # no {ext}
    "{title} ({year})/{title} ({year}).mkv{ext}",
])
def test_templates_cannot_change_the_extension(template):
    s = dict(S, organise_movie_template=template)
    assert organise.target_for(s, "/dl/Clip.2024.mp4")[0] == "/movies/Clip (2024)/Clip (2024).mp4"


def test_fix_template():
    assert organise.fix_template("{title} ({year}).mkv") == ("{title} ({year}){ext}", True)
    assert organise.fix_template("{show}/{show} - S{season:02d}E{episode:02d}") == \
        ("{show}/{show} - S{season:02d}E{episode:02d}{ext}", True)
    assert organise.fix_template(organise.SETTINGS_DEFAULTS["organise_tv_template"])[1] is False


def test_saving_a_template_with_mkv_is_corrected(lib):
    client, Session, media = lib
    r = client.put("/api/pipeline/organise", json={"organise_movie_template": "{title} ({year})/{title} ({year}).mkv"})
    body = r.json()
    assert body["organise_movie_template"] == "{title} ({year})/{title} ({year}){ext}"
    assert "keep their own extension" in body["warnings"]["organise_movie_template"]
    f = media / "Clip.2024.mp4"
    f.write_bytes(b"x")
    fid = store.upsert_file(str(f), f.name, 1, 60.0, "hevc", False)
    out = client.post(f"/api/media/files/{fid}/organise", json={"audience": "general"}).json()
    assert out["ok"] and out["path"].endswith("Clip (2024).mp4")


def test_library_rename_keeps_extension_and_dotted_names(lib):
    client, Session, media = lib
    f = media / "clip.mp4"
    f.write_bytes(b"x")
    fid = store.upsert_file(str(f), f.name, 1, 60.0, "hevc", False)
    assert client.post(f"/api/media/files/{fid}/rename", json={"new_name": "Rodriguez 03.07"}).json()["filename"] \
        == "Rodriguez 03.07.mp4"
    assert client.post(f"/api/media/files/{fid}/rename", json={"new_name": "New.mkv"}).json()["filename"] == "New.mp4"
