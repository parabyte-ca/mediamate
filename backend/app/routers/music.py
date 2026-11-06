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
