"""Music on the Watchlist: find artists (MusicBrainz), follow them, manage their albums."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import MusicAlbum, WatchlistItem
from ..schemas import WatchlistOut
from ..services import music, musicbrainz

logger = logging.getLogger(__name__)
router = APIRouter(tags=["music"])


class FollowRequest(BaseModel):
    mbid: str
    start: str = "new"              # new | latest | all
    include_eps: bool = False
    download_path_id: int | None = None


class AlbumUpdate(BaseModel):
    status: str                     # wanted | ignored


def _artist(db: Session, item_id: int) -> WatchlistItem:
    item = db.get(WatchlistItem, item_id)
    if item is None or item.kind != "artist":
        raise HTTPException(status_code=404, detail="Artist not found")
    return item


def _album_out(a: MusicAlbum, db: Session | None = None) -> dict:
    return {"id": a.id, "mbid": a.mbid, "title": a.title, "type": a.album_type, "release_date": a.release_date,
            "status": a.status, "released": music.released(a), "torrent_name": a.torrent_name,
            "quality": a.quality, "cover_url": musicbrainz.cover_url(a.mbid),
            "completed_at": a.completed_at.isoformat() if a.completed_at else None,
            "problem": music.problem(db, a) if db is not None and a.status == "wanted" else None}


@router.get("/music/artists/search")
def search_artists(q: str):
    if not q.strip():
        return []
    try:
        return musicbrainz.search_artists(q.strip())
    except musicbrainz.MusicBrainzUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"MusicBrainz is unavailable right now ({exc})")


@router.post("/watchlist/from-musicbrainz", response_model=WatchlistOut, status_code=201)
def follow_artist(body: FollowRequest, db: Session = Depends(get_db)):
    try:
        item = music.add_artist(db, body.mbid, body.start, body.include_eps, body.download_path_id)
    except music.AlreadyFollowing:
        raise HTTPException(status_code=409, detail="You already follow that artist.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except musicbrainz.MusicBrainzUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"MusicBrainz is unavailable right now ({exc})")
    row = WatchlistOut.model_validate(item)
    row.albums = music.summary(db, item)
    return row


@router.get("/watchlist/{item_id}/albums")
def list_albums(item_id: int, db: Session = Depends(get_db)):
    item = _artist(db, item_id)
    rows = (db.query(MusicAlbum).filter(MusicAlbum.watchlist_id == item.id)
            .order_by(MusicAlbum.release_date.desc()).all())
    music.settle(db, rows)
    return [_album_out(a, db) for a in rows]


@router.post("/watchlist/{item_id}/albums/refresh")
def refresh_albums(item_id: int, db: Session = Depends(get_db)):
    item = _artist(db, item_id)
    return {"new": music.refresh_albums(db, item, force=True), "albums": music.summary(db, item)}


@router.patch("/music/albums/{album_id}")
def update_album(album_id: int, body: AlbumUpdate, db: Session = Depends(get_db)):
    album = db.get(MusicAlbum, album_id)
    if album is None:
        raise HTTPException(status_code=404, detail="Album not found")
    if body.status not in ("wanted", "ignored", "have"):
        raise HTTPException(status_code=422, detail="status must be wanted, ignored or have")
    if album.status == "grabbed" and body.status != "have":
        raise HTTPException(status_code=409, detail="It's downloading; cancel it on Activity first.")
    album.status = body.status
    db.commit()
    return _album_out(album, db)


@router.post("/music/albums/{album_id}/search")
def search_album_now(album_id: int, db: Session = Depends(get_db)):
    """Look for an album right now (even before its release date) and download it."""
    album = db.get(MusicAlbum, album_id)
    if album is None:
        raise HTTPException(status_code=404, detail="Album not found")
    item = _artist(db, album.watchlist_id)
    if album.status == "ignored":
        album.status = "wanted"
        db.commit()
    try:
        return music.grab(db, item, album, force=True)
    except Exception as exc:
        logger.error("Album grab failed for %r: %s", album.title, exc)
        raise HTTPException(status_code=502, detail=f"Could not start the download: {exc}")


# ── the music library ───────────────────────────────────────────────────────

import json  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402

from fastapi import Body  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from ..models import MusicLibAlbum, MusicTrack  # noqa: E402
from ..services import music_library  # noqa: E402

_scan = {"running": False, "last": None}


def _lib_album_out(a: MusicLibAlbum) -> dict:
    return {"id": a.id, "folder": a.folder, "artist": a.artist, "title": a.title, "year": a.year,
            "format": a.format, "tracks": a.tracks, "size_bytes": a.size_bytes, "duration_sec": a.duration_sec,
            "has_cover": bool(a.cover), "mb_release_group_id": a.mb_release_group_id,
            "issues": json.loads(a.issues or "[]"), "rating": a.rating,
            "collections": json.loads(a.collections or "[]"), "plex_dirty": a.plex_dirty,
            "scanned_at": a.scanned_at.isoformat() if a.scanned_at else None}


def _lib_album(db: Session, album_id: int) -> MusicLibAlbum:
    a = db.get(MusicLibAlbum, album_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Album not found")
    return a


@router.get("/music/library/albums")
def library_albums(db: Session = Depends(get_db)):
    rows = db.query(MusicLibAlbum).order_by(MusicLibAlbum.artist, MusicLibAlbum.year, MusicLibAlbum.title).all()
    return [_lib_album_out(a) for a in rows]


@router.get("/music/library/albums/{album_id}")
def library_album(album_id: int, db: Session = Depends(get_db)):
    a = _lib_album(db, album_id)
    tracks = (db.query(MusicTrack).filter(MusicTrack.album_id == a.id)
              .order_by(MusicTrack.disc_no, MusicTrack.track_no, MusicTrack.path).all())
    return {**_lib_album_out(a), "track_list": [
        {"id": t.id, "title": t.title or os.path.splitext(os.path.basename(t.path))[0], "artist": t.artist,
         "track_no": t.track_no, "disc_no": t.disc_no, "format": t.format, "bitrate": t.bitrate,
         "lossless": t.lossless, "duration_sec": t.duration_sec, "size_bytes": t.size_bytes,
         "file": os.path.basename(t.path)} for t in tracks]}


@router.get("/music/library/albums/{album_id}/cover")
def library_cover(album_id: int, db: Session = Depends(get_db)):
    a = _lib_album(db, album_id)
    path = os.path.join(a.folder, a.cover) if a.cover else None
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No cover")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/music/library/tracks/{track_id}/stream")
def library_stream(track_id: int, db: Session = Depends(get_db)):
    t = db.get(MusicTrack, track_id)
    if t is None or not os.path.isfile(t.path):
        raise HTTPException(status_code=404, detail="Track not found")
    return FileResponse(t.path)


@router.post("/music/library/scan")
def library_scan():
    if _scan["running"]:
        return {"ok": True, "running": True}

    def run():
        try:
            _scan["last"] = music_library.scan()
        except Exception as exc:
            logger.exception("Music scan failed")
            _scan["last"] = {"ok": False, "msg": str(exc)}
        finally:
            _scan["running"] = False
    _scan["running"] = True
    if music_library.IMPORT_SYNC:
        run()
    else:
        threading.Thread(target=run, name="music-scan", daemon=True).start()
    return {"ok": True, "running": _scan["running"]}


@router.get("/music/library/status")
def library_status(db: Session = Depends(get_db)):
    return {"running": _scan["running"], "last": _scan["last"], "roots": music_library.roots(db),
            "music_dir": music_library.music_dir(db)}


def _album_files(db: Session, a: MusicLibAlbum) -> list[str]:
    return [t.path for t in db.query(MusicTrack).filter(MusicTrack.album_id == a.id).order_by(MusicTrack.path)
            if os.path.isfile(t.path)]


@router.post("/music/library/albums/{album_id}/fix-tags")
def library_fix_tags(album_id: int, db: Session = Depends(get_db)):
    a = _lib_album(db, album_id)
    files = _album_files(db, a)
    if not files:
        raise HTTPException(status_code=404, detail="The album's files are gone; scan again")
    try:
        res = music_library.fix_tags(files, a.artist or "", a.title or "", a.mb_release_group_id)
    except musicbrainz.MusicBrainzUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"MusicBrainz is unavailable right now ({exc})")
    music_library.scan_folder(db, a.folder)
    music_library.mark_duplicates(db)
    db.commit()
    return res


@router.post("/music/library/albums/{album_id}/cover")
def library_get_cover(album_id: int, db: Session = Depends(get_db)):
    """Find a cover for an album without one (embedded artwork, else MusicBrainz). Tags are untouched."""
    a = _lib_album(db, album_id)
    if not os.path.isdir(a.folder):
        raise HTTPException(status_code=404, detail="The album's folder is gone; scan again")
    res = music_library.ensure_cover(a.folder, _album_files(db, a), a.artist, a.title, a.mb_release_group_id)
    if res.get("unavailable"):
        raise HTTPException(status_code=503, detail="MusicBrainz is unavailable right now; try again shortly")
    a.cover = res.get("cover") or music_library.cover_in(a.folder)
    db.commit()
    msg = {"existing": "It already has a cover", "embedded": "Cover taken from the music files",
           "musicbrainz": "Cover downloaded from MusicBrainz"}.get(res.get("source"),
                                                                    "No cover found for this album")
    return {"ok": bool(a.cover), "source": res.get("source"), "msg": msg}


@router.post("/music/library/albums/{album_id}/organise")
def library_organise(album_id: int, db: Session = Depends(get_db)):
    a = _lib_album(db, album_id)
    root = music_library.music_dir(db)
    if not root or not os.path.isdir(root):
        raise HTTPException(status_code=400, detail="Choose a music library first: Settings → Folders, edit a folder, Library for: Music library")
    files = _album_files(db, a)
    res = music_library.organise(db, files, root, keep_source=music_library.in_download_folder(db, a.folder),
                                 tidy=a.folder not in music_library.roots(db))
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("msg"))
    old = a.folder
    if res["folder"] != old:
        music_library.scan_folder(db, old)
    music_library.scan_folder(db, os.path.normpath(res["folder"]))
    music_library.mark_duplicates(db)
    db.commit()
    return res


@router.post("/music/library/albums/{album_id}/rating")
def library_rate(album_id: int, body: dict = Body(default={})):
    from ..services import plex_sync
    stars = body.get("stars")
    try:
        return plex_sync.edit(album_id, rating=None if stars in (None, "", 0) else stars, kind="music")
    except KeyError:
        raise HTTPException(status_code=404, detail="Album not found")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/music/library/albums/{album_id}/collections")
def library_collections(album_id: int, body: dict = Body(default={})):
    from ..services import plex_sync
    try:
        return plex_sync.edit(album_id, add=body.get("add") or [], remove=body.get("remove") or [], kind="music")
    except KeyError:
        raise HTTPException(status_code=404, detail="Album not found")


@router.get("/music/library/collections")
def library_collection_names():
    from ..services import plex_sync
    return {"names": plex_sync.collection_names("music")}


# ── editing tags and artwork by hand ────────────────────────────────────────

from fastapi import File, Form, UploadFile  # noqa: E402

MAX_COVER_BYTES = 15 * 1024 * 1024


class TagSave(BaseModel):
    album: dict = {}
    tracks: list[dict] = []
    live: dict | None = None


class CoverPick(BaseModel):
    mbid: str
    embed: bool = False


def _paths_ok(db: Session, a: MusicLibAlbum, paths: list[str]) -> None:
    allowed = {t.path for t in db.query(MusicTrack).filter(MusicTrack.album_id == a.id)}
    if any(p not in allowed for p in paths):
        raise HTTPException(status_code=400, detail="Those files aren't part of this album; scan again")


def _rescan(db: Session, a: MusicLibAlbum) -> None:
    music_library.scan_folder(db, a.folder)
    music_library.mark_duplicates(db)
    db.commit()


@router.get("/music/library/albums/{album_id}/tags")
def album_tags(album_id: int, db: Session = Depends(get_db)):
    a = _lib_album(db, album_id)
    files = _album_files(db, a)
    if not files:
        raise HTTPException(status_code=404, detail="The album's files are gone; scan again")
    return music_library.album_tags(files)


@router.get("/music/library/albums/{album_id}/from-names")
def album_from_names(album_id: int, db: Session = Depends(get_db)):
    """Titles and track numbers guessed from the file names (nothing is saved)."""
    a = _lib_album(db, album_id)
    return [{"path": p, **music_library.from_name(p)} for p in _album_files(db, a)]


@router.put("/music/library/albums/{album_id}/tags")
def save_album_tags(album_id: int, body: TagSave, db: Session = Depends(get_db)):
    a = _lib_album(db, album_id)
    _paths_ok(db, a, [t.get("path") for t in body.tracks])
    for t in body.tracks:
        for k in ("track_no", "disc_no"):
            v = t.get(k)
            if v not in (None, "") and (not str(v).isdigit() or not 0 < int(v) < 1000):
                raise HTTPException(status_code=422, detail=f"{k.replace('_no', '')} numbers must be 1 to 999")
            t[k] = int(v) if v not in (None, "") else None
    res = music_library.save_album_tags(body.album, body.tracks, body.live)
    _rescan(db, a)
    res["msg"] = (f"Saved tags on {res['saved']} track{'s' if res['saved'] != 1 else ''}"
                  + (f"; couldn't write {', '.join(res['failed'][:3])}" if res["failed"] else ""))
    return res


def _apply_cover(db: Session, a: MusicLibAlbum, data: bytes, embed: bool) -> dict:
    try:
        name = music_library.replace_cover(a.folder, data)
        emb = music_library.embed_cover(_album_files(db, a), data) if embed else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Couldn't save the cover: {exc}")
    _rescan(db, a)
    msg = f"Cover saved as {name}"
    if emb:
        msg += f" and put in {emb['embedded']} track{'s' if emb['embedded'] != 1 else ''}"
        if emb["skipped"]:
            msg += f" ({len(emb['skipped'])} file type{'s' if len(emb['skipped']) != 1 else ''} can't hold art)"
    return {"ok": True, "cover": name, "msg": msg}


@router.post("/music/library/albums/{album_id}/cover/upload")
async def upload_cover(album_id: int, file: UploadFile = File(...), embed: bool = Form(False),
                       db: Session = Depends(get_db)):
    a = _lib_album(db, album_id)
    data = await file.read(MAX_COVER_BYTES + 1)
    if len(data) > MAX_COVER_BYTES:
        raise HTTPException(status_code=413, detail="That image is over 15 MB")
    return _apply_cover(db, a, data, embed)


@router.get("/music/library/albums/{album_id}/cover/candidates")
def cover_candidates(album_id: int, q: str | None = None, db: Session = Depends(get_db)):
    a = _lib_album(db, album_id)
    try:
        return musicbrainz.search_release_groups(a.artist, a.title, (q or "").strip() or None)
    except musicbrainz.MusicBrainzUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"MusicBrainz is unavailable right now ({exc})")


@router.post("/music/library/albums/{album_id}/cover/musicbrainz")
def cover_from_musicbrainz(album_id: int, body: CoverPick, db: Session = Depends(get_db)):
    a = _lib_album(db, album_id)
    data = musicbrainz.cover_bytes(body.mbid)
    if not data:
        raise HTTPException(status_code=404, detail="That release has no cover on MusicBrainz; pick another")
    res = _apply_cover(db, a, data, body.embed)
    a.mb_release_group_id = a.mb_release_group_id or body.mbid
    db.commit()
    return res


@router.post("/music/library/albums/{album_id}/cover/embed")
def embed_current_cover(album_id: int, db: Session = Depends(get_db)):
    """Put the album's current cover image inside its tracks."""
    a = _lib_album(db, album_id)
    if not a.cover or not os.path.isfile(os.path.join(a.folder, a.cover)):
        raise HTTPException(status_code=404, detail="This album has no cover to put in the files")
    with open(os.path.join(a.folder, a.cover), "rb") as fh:
        data = fh.read()
    try:
        emb = music_library.embed_cover(_album_files(db, a), data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _rescan(db, a)
    return {"ok": True, "msg": f"Cover put in {emb['embedded']} track{'s' if emb['embedded'] != 1 else ''}",
            **emb}


# ── many albums at once ─────────────────────────────────────────────────────


class AlbumBulk(BaseModel):
    action: str                     # covers | fix_tags | organise | embed | rate | collect
    album_ids: list[int]
    stars: int | None = None
    add: list[str] = []
    remove: list[str] = []


@router.post("/music/library/bulk")
def library_bulk(body: AlbumBulk):
    """Rate or collect many albums now; covers, tags, organising and embedding run in
    the background (see /music/library/bulk/status)."""
    from ..services import music_batch, plex_sync
    if body.action in ("rate", "collect"):
        done, sent, errors = 0, 0, []
        for album_id in dict.fromkeys(body.album_ids):
            try:
                if body.action == "rate":
                    r = plex_sync.edit(album_id, rating=body.stars or None, kind="music")
                else:
                    r = plex_sync.edit(album_id, add=body.add, remove=body.remove, kind="music")
            except KeyError:
                continue
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))
                continue
            done += 1
            sent += r.get("plex") == "sent"
        return {"ok": not errors, "done": done, "sent": sent, "errors": errors}
    try:
        return music_batch.start(body.action, body.album_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/music/library/bulk/status")
def library_bulk_status():
    from ..services import music_batch
    return music_batch.status()


# ── music doctor ────────────────────────────────────────────────────────────

def _doctor_out(db: Session, report: dict | None) -> dict:
    from ..services import doctor, music_batch
    return {"auto_fix": doctor.auto_fix_enabled(db), "report": report, "batch": music_batch.status()}


@router.get("/music/doctor")
def music_doctor_get(db: Session = Depends(get_db)):
    from ..services import music_doctor
    return _doctor_out(db, music_doctor.last_report(db))


@router.post("/music/doctor/run")
def music_doctor_run(db: Session = Depends(get_db)):
    from ..services import music_doctor
    return _doctor_out(db, music_doctor.run(db))


class DoctorFixIn(BaseModel):
    ids: list[str]


@router.post("/music/doctor/fix")
def music_doctor_fix(body: DoctorFixIn, db: Session = Depends(get_db)):
    from ..services import music_doctor
    r = music_doctor.fix_ids(db, [str(i) for i in body.ids])
    return {"done": r["done"], "errors": r["errors"], "started": r["started"], **_doctor_out(db, r["report"])}
