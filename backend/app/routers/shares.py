"""Share links: a household link opens an item inside MediaMate; a public link
(/s/<token>) lets anyone with it watch a video or listen to an album in the browser,
without logging in, until it expires or is revoked. Public responses carry titles only,
never paths or ids, and a token only ever reaches its own item."""
import json
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import MusicLibAlbum, MusicTrack, Person, Setting, ShareLink

router = APIRouter()
DAYS = (0, 1, 7, 30)                     # 0: until revoked
GONE = "This link has expired or was removed."
_hits: dict[str, deque] = defaultdict(deque)
RATE = 60                                # page loads a minute per address


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _base(db: Session, request: Request) -> str:
    """The public address (Settings → Sharing), or "" so the page adds the address it's
    open on (a reverse proxy can hide the real one from the server)."""
    row = db.get(Setting, "public_url")
    return (row.value if row and row.value else "").strip().rstrip("/")


def _out(db: Session, s: ShareLink, request: Request) -> dict:
    who = db.get(Person, s.person_id) if s.person_id else None
    return {"id": s.id, "kind": s.kind, "target_id": s.target_id, "title": s.title, "adult": bool(s.adult),
            "url": f"{_base(db, request)}/s/{s.token}", "shared_by": who.name if who else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            "views": s.views or 0, "teaser": bool(s.clip)}


def _starts(s: ShareLink) -> list[float] | None:
    try:
        v = json.loads(s.clip) if s.clip else None
        return [float(x) for x in v] if isinstance(v, list) and v else None
    except (ValueError, TypeError):
        return None


def _teaser_row(file_id: int):
    row = _video(None, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    return row


def _video(db: Session, file_id: int):
    """The media file's row if it exists and lives under a media folder, else None."""
    from ..routers.media import _servable
    return _servable(file_id)


def _is_adult(db: Session, path: str) -> bool:
    from ..services import adult, organise
    try:
        organise.guard(path, db)
    except organise.OrganiseError:
        return True                       # protected folder or adult-looking name
    return bool(adult.looks_adult(path))


@router.post("/shares", status_code=201)
def create_share(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    from ..services import people
    from ..services.media import teaser
    kind, target, days = body.get("kind"), body.get("id"), body.get("days", 7)
    clip = None
    if kind not in ("video", "album") or not isinstance(target, int):
        raise HTTPException(status_code=400, detail="kind (video or album) and id are needed")
    if days not in DAYS:
        raise HTTPException(status_code=400, detail="days must be 1, 7, 30 or 0 (until revoked)")
    if kind == "video":
        row = _video(db, target)
        if row is None:
            raise HTTPException(status_code=404, detail="File not found")
        title = os.path.splitext(row["filename"])[0]
        adult = _is_adult(db, row["path"])
        if body.get("teaser") is not None:
            if not adult:
                raise HTTPException(status_code=400, detail="Teasers are for adult videos")
            try:
                starts = teaser.valid((body.get("teaser") or {}).get("starts"), row["duration_sec"])
            except teaser.TeaserError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            if teaser.ready(target, row["path"], starts) is None:
                teaser.start(target, row["path"], starts, row["duration_sec"])   # finishes in the background
            clip = json.dumps(starts)
    else:
        a = db.get(MusicLibAlbum, target)
        if a is None:
            raise HTTPException(status_code=404, detail="Album not found")
        title = " – ".join(x for x in (a.artist, a.title) if x) or os.path.basename(a.folder)
        adult = False
    me = people.current(db, request)
    s = ShareLink(token=secrets.token_urlsafe(32), kind=kind, target_id=target, title=title, adult=adult, clip=clip,
                  person_id=me.id if me else None,
                  expires_at=_now() + timedelta(days=days) if days else None)
    db.add(s)
    db.commit()
    return _out(db, s, request)


@router.get("/shares/teaser/{file_id}")
def teaser_moments(file_id: int, db: Session = Depends(get_db)):
    """Whether this video can have a teaser (adult only), and the default moments."""
    from ..services.media import teaser
    row = _video(db, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    starts = teaser.default_moments(row["duration_sec"])
    return {"adult": _is_adult(db, row["path"]), "duration_sec": row["duration_sec"],
            "starts": starts, "clip_sec": teaser.clip_len(row["duration_sec"] or 0, len(starts))}


def _picked(starts, duration) -> list[float]:
    from ..services.media import teaser
    try:
        if isinstance(starts, str):
            starts = [float(x) for x in starts.split(",") if x.strip()]
        return teaser.valid(starts, duration)
    except ValueError:
        raise HTTPException(status_code=400, detail="Moments must be numbers")
    except teaser.TeaserError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/shares/teaser/{file_id}/build")
def teaser_build(file_id: int, body: dict = Body(...)):
    """Start (or join) making the teaser for these moments; poll /shares/teaser/build/{key}."""
    from ..services.media import teaser
    row = _teaser_row(file_id)
    k = teaser.start(file_id, row["path"], _picked(body.get("starts"), row["duration_sec"]), row["duration_sec"])
    return {"key": k, **(teaser.status(k) or {})}


@router.get("/shares/teaser/build/{key}")
def teaser_build_status(key: str):
    from ..services.media import teaser
    st = teaser.status(key) if key.replace("-", "").isalnum() else None
    if st is None:
        raise HTTPException(status_code=404, detail="No such teaser")
    return {"key": key, **st}


@router.get("/shares/teaser/{file_id}/clip")
def teaser_preview(file_id: int, starts: str):
    """The finished teaser for these moments (comma-separated seconds). Never builds:
    409 until POST /build has finished."""
    from ..services.media import teaser
    row = _teaser_row(file_id)
    mp4 = teaser.ready(file_id, row["path"], _picked(starts, row["duration_sec"]))
    if mp4 is None:
        raise HTTPException(status_code=409, detail="The teaser is still being made")
    return FileResponse(mp4, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/shares")
def list_shares(request: Request, db: Session = Depends(get_db)):
    now = _now()
    rows = (db.query(ShareLink).filter(ShareLink.revoked_at.is_(None))
            .order_by(ShareLink.created_at.desc()).all())
    return [_out(db, s, request) for s in rows if s.expires_at is None or s.expires_at > now]


@router.delete("/shares/{share_id}")
def revoke_share(share_id: int, db: Session = Depends(get_db)):
    s = db.get(ShareLink, share_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Not found")
    s.revoked_at = _now()
    db.commit()
    return {"ok": True}


def purge(db: Session) -> int:
    """Forget links that expired or were revoked over 30 days ago."""
    cut = _now() - timedelta(days=30)
    n = db.query(ShareLink).filter((ShareLink.revoked_at < cut) | (ShareLink.expires_at < cut)).delete(
        synchronize_session=False)
    db.commit()
    from ..services import music_covers
    from ..services.media import teaser
    teaser.prune()
    music_covers.prune()
    return n


# ── public (no login) ────────────────────────────────────────────────────────


def _live(db: Session, token: str) -> ShareLink:
    s = db.query(ShareLink).filter(ShareLink.token == token).first() if token and len(token) < 100 else None
    if s is None or s.revoked_at is not None or (s.expires_at is not None and s.expires_at <= _now()):
        raise HTTPException(status_code=404, detail=GONE)
    return s


def _tracks(db: Session, album_id: int) -> list[MusicTrack]:
    return (db.query(MusicTrack).filter(MusicTrack.album_id == album_id)
            .order_by(MusicTrack.disc_no, MusicTrack.track_no, MusicTrack.path).all())


def _limit(request: Request) -> None:
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    q, now = _hits[ip], time.monotonic()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE:
        raise HTTPException(status_code=429, detail="Too many requests; try again in a minute.")
    q.append(now)


@router.get("/public/s/{token}")
def public_meta(token: str, request: Request, db: Session = Depends(get_db)):
    _limit(request)
    s = _live(db, token)
    out = {"kind": s.kind, "title": s.title, "adult": bool(s.adult),
           "expires_at": s.expires_at.isoformat() if s.expires_at else None}
    if s.kind == "video":
        row = _video(db, s.target_id)
        if row is None:
            raise HTTPException(status_code=404, detail=GONE)
        starts = _starts(s)
        if starts:
            from ..services.media import teaser
            out["teaser"] = True
            out["ready"] = teaser.ready(s.target_id, row["path"], starts) is not None
            if not out["ready"]:
                teaser.start(s.target_id, row["path"], starts, row["duration_sec"])
            out["duration_sec"] = round(teaser.clip_len(row["duration_sec"] or 0, len(starts)) * len(starts), 1)
        else:
            out["duration_sec"] = row["duration_sec"]
    else:
        a = db.get(MusicLibAlbum, s.target_id)
        if a is None:
            raise HTTPException(status_code=404, detail=GONE)
        out["has_cover"] = bool(a.cover and os.path.isfile(os.path.join(a.folder, a.cover)))
        out["tracks"] = [{"n": i + 1, "title": t.title or os.path.splitext(os.path.basename(t.path))[0],
                          "artist": t.artist, "duration_sec": t.duration_sec}
                         for i, t in enumerate(_tracks(db, a.id))]
    s.views = (s.views or 0) + 1
    s.last_viewed_at = _now()
    db.commit()
    return out


@router.get("/public/s/{token}/video")
def public_video(token: str, db: Session = Depends(get_db)):
    s = _live(db, token)
    if s.kind != "video" or _video(db, s.target_id) is None:
        raise HTTPException(status_code=404, detail=GONE)
    starts = _starts(s)
    if starts:
        from ..services.media import teaser
        row = _teaser_row(s.target_id)
        mp4 = teaser.ready(s.target_id, row["path"], starts)
        if mp4 is None:                             # still being made, or the cache was cleared
            teaser.start(s.target_id, row["path"], starts, row["duration_sec"])
            raise HTTPException(status_code=503, detail="The teaser is being made. Try again in a minute.",
                                headers={"Retry-After": "10"})
        try:
            os.utime(mp4)                          # keeps it from being pruned while in use
        except OSError:
            pass
        return FileResponse(mp4, media_type="video/mp4", content_disposition_type="inline",
                            headers={"Cache-Control": "no-store"})
    row = _video(db, s.target_id)
    return FileResponse(row["path"], content_disposition_type="inline", headers={"Cache-Control": "no-store"})


@router.get("/public/s/{token}/play")
def public_play(token: str, start: float = 0.0, db: Session = Depends(get_db)):
    from . import media
    s = _live(db, token)
    if s.kind != "video" or s.clip:               # a teaser never reaches the whole video
        raise HTTPException(status_code=404, detail=GONE)
    return media.play_file(s.target_id, start)


@router.get("/public/s/{token}/track/{n}")
def public_track(token: str, n: int, db: Session = Depends(get_db)):
    s = _live(db, token)
    tracks = _tracks(db, s.target_id) if s.kind == "album" else []
    if not 1 <= n <= len(tracks) or not os.path.isfile(tracks[n - 1].path):
        raise HTTPException(status_code=404, detail="Track not found")
    return FileResponse(tracks[n - 1].path, headers={"Cache-Control": "no-store"})


@router.get("/public/s/{token}/cover")
def public_cover(token: str, db: Session = Depends(get_db)):
    s = _live(db, token)
    a = db.get(MusicLibAlbum, s.target_id) if s.kind == "album" else None
    path = os.path.join(a.folder, a.cover) if a and a.cover else None
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No cover")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})
