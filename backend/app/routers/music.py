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


def _album_out(a: MusicAlbum) -> dict:
    return {"id": a.id, "mbid": a.mbid, "title": a.title, "type": a.album_type, "release_date": a.release_date,
            "status": a.status, "released": music.released(a), "torrent_name": a.torrent_name,
            "quality": a.quality, "cover_url": musicbrainz.cover_url(a.mbid),
            "completed_at": a.completed_at.isoformat() if a.completed_at else None}


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
    return [_album_out(a) for a in rows]


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
    return _album_out(album)


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


@router.post("/music/library/albums/{album_id}/organise")
def library_organise(album_id: int, db: Session = Depends(get_db)):
    a = _lib_album(db, album_id)
    root = music_library.music_dir(db)
    if not root or not os.path.isdir(root):
        raise HTTPException(status_code=400, detail="Set the music library folder in Settings → Music first")
    files = _album_files(db, a)
    res = music_library.organise(db, files, root, keep_source=music_library.in_download_folder(db, a.folder))
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
