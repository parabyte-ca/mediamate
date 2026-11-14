"""The music library: scanning, tags, placing finished downloads, and checks.

The video Library's mechanics, applied to music:
* **Roots:** folders whose target is "music" (Settings → Folders), plus the music
  library folder (``organise_music_dir``).
* **Scan:** each folder of audio files becomes an album row (``music_lib_albums``)
  with its tracks (``music_tracks``), read with mutagen.
* **Import:** a finished album download is linked (or copied) into the music library
  folder as ``Artist/Album (Year)/01 - Title.flac``. The torrent keeps seeding from the
  original files. Missing tags are filled from MusicBrainz first, and the front cover
  is saved as cover.jpg.
* **Checks:** untagged files, missing tracks, low-bitrate copies (lossy under 220 kbps)
  and duplicate albums are flagged on each album. Fix tags and Organise fix the
  first two; duplicates are left to the user.
"""
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif", ".wma",
              ".ape", ".wv", ".alac", ".dsf"}
LOSSLESS_EXTS = {".flac", ".wav", ".aiff", ".aif", ".ape", ".wv", ".alac", ".dsf"}
IMAGE_NAMES = ("cover", "folder", "front", "albumart", "album")
IMAGE_EXTS = (".jpg", ".jpeg", ".png")
LOW_BITRATE = 220
DIR_KEY = "organise_music_dir"
IMPORT_SYNC = False                     # tests run imports inline
_scan_lock = threading.Lock()


# ── tags ────────────────────────────────────────────────────────────────────


def _first(tags, key) -> str | None:
    try:
        v = tags.get(key)
    except Exception:
        return None
    if isinstance(v, list):
        v = v[0] if v else None
    v = str(v).strip() if v is not None else None
    return v or None


def _num(v: str | None) -> tuple[int | None, int | None]:
    m = re.match(r"\s*(\d+)(?:\s*/\s*(\d+))?", v or "")
    return (int(m.group(1)), int(m.group(2)) if m.group(2) else None) if m else (None, None)


def read(path: str) -> dict:
    """Tags and stream info for one audio file (missing values are None)."""
    import mutagen
    ext = os.path.splitext(path)[1].lower()
    out = {"path": path, "format": ext.lstrip("."), "lossless": ext in LOSSLESS_EXTS, "size_bytes": None,
           "mtime": None, "duration_sec": None, "bitrate": None, "title": None, "artist": None,
           "album_artist": None, "album": None, "year": None, "track_no": None, "track_total": None,
           "disc_no": None, "mb_release_group_id": None, "date": None, "genre": None, "comment": None}
    try:
        st = os.stat(path)
        out["size_bytes"], out["mtime"] = st.st_size, st.st_mtime
    except OSError:
        return out
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        f = None
    if f is None:
        return out
    info = getattr(f, "info", None)
    if info is not None:
        out["duration_sec"] = float(getattr(info, "length", 0) or 0) or None
        br = getattr(info, "bitrate", 0) or 0
        out["bitrate"] = int(br / 1000) if br else None
    tags = f.tags or {}
    out["title"] = _first(tags, "title")
    out["artist"] = _first(tags, "artist")
    out["album_artist"] = _first(tags, "albumartist") or out["artist"]
    out["album"] = _first(tags, "album")
    y = re.match(r"\d{4}", _first(tags, "date") or _first(tags, "originaldate") or "")
    out["year"] = int(y.group(0)) if y else None
    out["track_no"], out["track_total"] = _num(_first(tags, "tracknumber"))
    if out["track_total"] is None:
        out["track_total"] = _num(_first(tags, "tracktotal") or _first(tags, "totaltracks"))[0]
    out["disc_no"] = _num(_first(tags, "discnumber"))[0]
    out["mb_release_group_id"] = _first(tags, "musicbrainz_releasegroupid")
    out["date"] = _first(tags, "date")
    out["genre"] = _first(tags, "genre")
    out["comment"] = _first(tags, "comment") or _first(tags, "description")
    return out


def _register_id3_comment() -> None:
    """EasyID3 has no "comment" key; map it to the COMM frame so MP3s take comments too."""
    from mutagen.easyid3 import EasyID3
    if "comment" in EasyID3.valid_keys:
        return
    from mutagen.id3 import COMM

    def get(id3, key):
        return [t for fr in id3.getall("COMM") for t in fr.text]

    def set_(id3, key, value):
        id3.delall("COMM")
        id3.add(COMM(encoding=3, lang="eng", desc="", text=value))

    def delete(id3, key):
        id3.delall("COMM")
    EasyID3.RegisterKey("comment", get, set_, delete)


def write(path: str, values: dict) -> bool:
    """Set tags (title, artist, albumartist, album, date, genre, comment, tracknumber,
    discnumber, musicbrainz_releasegroupid). None leaves a tag alone; "" removes it.
    True if saved."""
    import mutagen
    _register_id3_comment()
    try:
        f = mutagen.File(path, easy=True)
        if f is None:
            return False
        if f.tags is None:
            f.add_tags()
        for k, v in values.items():
            if v is None:
                continue
            try:
                if str(v).strip() == "":
                    if k in f:
                        del f[k]
                else:
                    f[k] = [str(v).strip()]
            except (KeyError, ValueError, TypeError):
                continue                          # a key this format can't hold
        f.save()
        return True
    except Exception as exc:
        logger.warning("Couldn't write tags to %s: %s", path, exc)
        return False


# ── finding files ───────────────────────────────────────────────────────────


def audio_files(folder: str) -> list[str]:
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        out += [os.path.join(root, f) for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTS]
    return sorted(out, key=_natural)


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def cover_in(folder: str) -> str | None:
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    imgs = [n for n in names if n.lower().endswith(IMAGE_EXTS)]
    for want in IMAGE_NAMES:
        for n in imgs:
            if os.path.splitext(n)[0].lower() == want:
                return n
    return imgs[0] if imgs else None


def files_added(files: list[str]) -> datetime | None:
    """When these files arrived: the newest modification time among them."""
    times = []
    for f in files:
        try:
            times.append(os.stat(f).st_mtime)
        except OSError:
            pass
    return datetime.fromtimestamp(max(times), timezone.utc) if times else None


def backfill_added(db) -> int:
    """Albums scanned before "Recently added" existed get the time their files arrived."""
    from ..models import MusicLibAlbum, MusicTrack
    n = 0
    for a in db.query(MusicLibAlbum).filter(MusicLibAlbum.added_at.is_(None)).all():
        paths = [t.path for t in db.query(MusicTrack.path).filter(MusicTrack.album_id == a.id)]
        a.added_at = files_added(paths) or a.scanned_at
        n += 1
        if n % 200 == 0:
            db.commit()
    db.commit()
    return n


def roots(db) -> list[str]:
    from ..models import DownloadPath, Setting
    from . import folders
    out = []
    lib = music_dir(db)
    if lib:
        out.append(lib)
    for dp in db.query(DownloadPath).filter(DownloadPath.target == "music").all():
        local = folders.local_of(db, dp)
        if local:
            out.append(local)
    seen, uniq = set(), []
    for r in out:
        n = os.path.normpath(r)
        if n not in seen and os.path.isdir(n):
            seen.add(n)
            uniq.append(n)
    return uniq


# ── scanning ────────────────────────────────────────────────────────────────


def _norm(t: str | None) -> str:
    t = (t or "").lower().replace("&", " and ")
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    return re.sub(r"^the\s+", "", re.sub(r"[^a-z0-9]+", " ", t).strip())


def _issues(tracks: list[dict]) -> list[str]:
    issues = []
    if any(not (t["title"] and t["artist"] and t["album"] and t["track_no"]) for t in tracks):
        issues.append("untagged")
    by_disc: dict[int, list[dict]] = {}
    for t in tracks:
        by_disc.setdefault(t["disc_no"] or 1, []).append(t)
    for ts in by_disc.values():
        nos = sorted(t["track_no"] for t in ts if t["track_no"])
        total = max((t["track_total"] or 0 for t in ts), default=0)
        if (total and len(ts) < total) or (nos and nos[-1] > len(set(nos))):
            issues.append("missing_tracks")
            break
    lossy = [t["bitrate"] for t in tracks if not t["lossless"] and t["bitrate"]]
    if lossy and sum(lossy) / len(lossy) < LOW_BITRATE:
        issues.append("low_bitrate")
    return issues


def scan_folder(db, folder: str, tracks_info: list[dict] | None = None):
    """Index one album folder (its audio files, not sub-folders' when those are albums)."""
    from ..models import MusicLibAlbum, MusicTrack
    files = [os.path.join(folder, f) for f in sorted(os.listdir(folder), key=_natural)
             if os.path.splitext(f)[1].lower() in AUDIO_EXTS] if os.path.isdir(folder) else []
    # Disc folders ("CD1", "Disc 2", bootleg "gd77-05-08d1") belong to the album above them.
    subs = [d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))] if os.path.isdir(folder) else []
    for sub in sorted(disc_dirs(subs), key=_natural):
        p = os.path.join(folder, sub)
        files += [os.path.join(p, f) for f in sorted(os.listdir(p), key=_natural)
                  if os.path.splitext(f)[1].lower() in AUDIO_EXTS]
    album = db.query(MusicLibAlbum).filter(MusicLibAlbum.folder == folder).first()
    if not files:
        if album is not None:
            db.query(MusicTrack).filter(MusicTrack.album_id == album.id).delete()
            db.delete(album)
        return None
    infos = tracks_info or [read(f) for f in files]
    if album is None:
        album = MusicLibAlbum(folder=folder)
        db.add(album)
        db.flush()
    pick = lambda k: next((t[k] for t in infos if t.get(k)), None)  # noqa: E731
    fmts = {t["format"] for t in infos}
    album.artist = pick("album_artist") or pick("artist") or os.path.basename(os.path.dirname(folder)) or None
    album.title = pick("album") or os.path.basename(folder)
    album.year = pick("year")
    album.format = fmts.pop() if len(fmts) == 1 else "mixed"
    album.tracks = len(infos)
    album.size_bytes = sum(t["size_bytes"] or 0 for t in infos)
    album.duration_sec = sum(t["duration_sec"] or 0 for t in infos)
    album.cover = cover_in(folder)
    album.mb_release_group_id = pick("mb_release_group_id") or album.mb_release_group_id
    issues = [i for i in json.loads(album.issues or "[]") if i == "duplicate"]
    album.issues = json.dumps(_issues(infos) + issues) if (_issues(infos) or issues) else None
    album.scanned_at = datetime.now(timezone.utc)
    if album.added_at is None:
        album.added_at = files_added(files)
    have = {t.path: t for t in db.query(MusicTrack).filter(MusicTrack.album_id == album.id)}
    for info in infos:
        row = have.pop(info["path"], None) or db.query(MusicTrack).filter(MusicTrack.path == info["path"]).first()
        if row is None:
            row = MusicTrack(path=info["path"], album_id=album.id)
            db.add(row)
        row.album_id = album.id
        for k in ("title", "artist", "track_no", "track_total", "disc_no", "format", "bitrate", "lossless",
                  "duration_sec", "size_bytes", "mtime"):
            setattr(row, k, info[k])
    for gone in have.values():
        db.delete(gone)
    return album


_DISC = re.compile(r"(?i)^(cd|disc|disk)\s*\d+$")
_DISC_SUFFIX = re.compile(r"(?i)^(?P<stem>.*?)[\s._-]*(?:cd|disc|disk|d)\s*(?P<n>\d{1,2})$")


def disc_dirs(names) -> set[str]:
    """Which of these sibling folders are discs of one album: "CD1", "Disc 2", or two or
    more that differ only by a disc suffix ("gd77-05-08d1", "gd77-05-08d2", "Show CD1")."""
    names = list(names)
    out = {n for n in names if _DISC.match(n)}
    groups: dict[str, list[str]] = {}
    for n in names:
        m = _DISC_SUFFIX.match(n)
        if m and m.group("stem").strip(" ._-"):
            groups.setdefault(m.group("stem").strip(" ._-").lower(), []).append(n)
    for members in groups.values():
        if len(members) >= 2:
            out.update(members)
    return out


def _album_folders(root: str, skip: set[str] = frozenset()) -> list[str]:
    folders = []
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".")
                         and os.path.normpath(os.path.join(here, d)) not in skip)
        discs = disc_dirs(dirs)
        dirs[:] = [d for d in dirs if d not in discs]      # read with the album above them
        has_audio = any(os.path.splitext(f)[1].lower() in AUDIO_EXTS for f in files)
        if has_audio or discs:
            folders.append(os.path.normpath(here))
    return folders


def download_folders_inside(db, rs: list[str]) -> set[str]:
    """Folders qBittorrent downloads into that sit inside a music root. With a music
    library folder set, finished albums are linked from them into the library, so the
    raw torrent folders aren't scanned as albums too."""
    from ..models import DownloadPath, Setting
    from . import folders
    if not music_dir(db):
        return set()
    out = set()
    for dp in db.query(DownloadPath).filter(DownloadPath.downloads.is_(True)).all():
        local = folders.local_of(db, dp)
        if not local:
            continue
        p = os.path.normpath(local)
        if any(p != r and p.startswith(r.rstrip(os.sep) + os.sep) for r in rs):
            out.add(p)
    return out


def mark_duplicates(db) -> int:
    from ..models import MusicLibAlbum
    albums = db.query(MusicLibAlbum).all()
    groups: dict[tuple, list] = {}
    for a in albums:
        groups.setdefault((_norm(a.artist), _norm(a.title)), []).append(a)
    n = 0
    for (artist, title), rows in groups.items():
        dup = bool(artist and title) and len(rows) > 1
        for a in rows:
            cur = [i for i in json.loads(a.issues or "[]") if i != "duplicate"]
            if dup:
                cur.append("duplicate")
                n += 1
            a.issues = json.dumps(cur) if cur else None
    return n


def scan(bind=None) -> dict:
    """Scan every music root. Returns {albums, tracks, removed, roots}."""
    from sqlalchemy.orm import Session
    from ..models import MusicLibAlbum, MusicTrack
    from .media import store
    if not _scan_lock.acquire(blocking=False):
        return {"ok": False, "msg": "A music scan is already running"}
    try:
        with Session(bind=bind or store._engine()) as db:
            rs = roots(db)
            skip = download_folders_inside(db, rs)
            seen = set()
            for r in rs:
                for folder in _album_folders(r, skip):
                    if scan_folder(db, folder) is not None:
                        seen.add(folder)
                    # Save each album as it's read: one save per folder of 1,000+ albums held
                    # the database for minutes, and Want / Download presses failed meanwhile.
                    db.commit()
            removed = 0
            for a in db.query(MusicLibAlbum).all():
                # Not found by this scan: gone, outside the roots, a disc now read with its
                # album, or inside a download folder that isn't scanned.
                if a.folder not in seen:
                    db.query(MusicTrack).filter(MusicTrack.album_id == a.id).delete()
                    db.delete(a)
                    removed += 1
            mark_duplicates(db)
            db.commit()
            covers = fill_covers(db)
            try:
                from . import music_follow
                music_follow.notify_after_scan(db)
            except Exception:
                logger.exception("Music upgrade notice failed")
            return {"ok": True, "roots": rs, "albums": db.query(MusicLibAlbum).count(),
                    "tracks": db.query(MusicTrack).count(), "removed": removed, "covers": covers}
    finally:
        _scan_lock.release()


# ── tags from MusicBrainz ───────────────────────────────────────────────────


def _order(infos: list[dict]) -> list[dict]:
    return sorted(infos, key=lambda t: (t["disc_no"] or 1, t["track_no"] or 10_000, _natural(t["path"])))


def fix_tags(files: list[str], artist: str, album: str, rg_mbid: str | None = None) -> dict:
    """Fill tags from MusicBrainz for an album's files (matched in order, when the
    track count matches a release). Saves cover.jpg when the folder has no picture.
    Returns {"ok", "tagged", "msg", "release_group"}."""
    from . import musicbrainz
    rg = rg_mbid or musicbrainz.find_release_group(artist, album)
    if not rg:
        return {"ok": False, "tagged": 0, "msg": f"MusicBrainz doesn't know {artist} – {album}"}
    infos = _order([read(f) for f in files])
    rel = next((r for r in musicbrainz.releases(rg) if len(r["tracks"]) == len(infos)), None)
    if rel is None:
        return {"ok": False, "tagged": 0, "release_group": rg,
                "msg": f"No release of {album} on MusicBrainz has {len(infos)} tracks"}
    multi = rel["discs"] > 1
    per_disc: dict[int, int] = {}
    for t in rel["tracks"]:
        per_disc[t["disc"]] = per_disc.get(t["disc"], 0) + 1
    tagged = 0
    for info, t in zip(infos, rel["tracks"]):
        ok = write(info["path"], {
            "title": t["title"], "artist": t["artist"], "albumartist": rel["artist"], "album": rel["title"],
            "date": rel["date"][:10] if rel["date"] else None,
            "tracknumber": f"{t['no']}/{per_disc[t['disc']]}",
            "discnumber": f"{t['disc']}/{rel['discs']}" if multi else None,
            "musicbrainz_releasegroupid": rg, "musicbrainz_albumid": rel["mbid"],
        })
        tagged += ok
    ensure_cover(os.path.dirname(infos[0]["path"]), [i["path"] for i in infos], rg_mbid=rg)
    return {"ok": tagged == len(infos), "tagged": tagged, "release_group": rg,
            "msg": f"Tagged {tagged} of {len(infos)} tracks from MusicBrainz"}


# ── covers ──────────────────────────────────────────────────────────────────

COVER_RETRY_DAYS = 30
SCAN_LOOKUPS = 25                  # MusicBrainz cover lookups per scan (1 request a second)


def embedded_cover(path: str) -> tuple[bytes, str] | None:
    """The front cover stored inside an audio file, as (data, ".jpg"/".png"), or None."""
    import base64
    import mutagen
    try:
        f = mutagen.File(path)
    except Exception:
        return None
    if f is None:
        return None
    pics = []
    for p in getattr(f, "pictures", None) or []:                      # FLAC
        pics.append((p.type, p.data, p.mime))
    tags = f.tags
    try:
        for k in list(tags.keys()) if tags is not None else []:
            v = tags[k]
            if str(k).startswith("APIC"):                               # MP3 (ID3)
                pics.append((getattr(v, "type", 0), v.data, v.mime))
            elif k == "covr":                                           # MP4 / M4A
                for c in v:
                    pics.append((3, bytes(c), "image/png" if getattr(c, "imageformat", 13) == 14 else "image/jpeg"))
            elif str(k).lower() == "metadata_block_picture":            # Ogg / Opus
                from mutagen.flac import Picture
                for b64 in v:
                    try:
                        p = Picture(base64.b64decode(b64))
                        pics.append((p.type, p.data, p.mime))
                    except Exception:
                        continue
    except Exception:
        pass
    pics = [p for p in pics if p[1]]
    if not pics:
        return None
    ptype, data, mime = sorted(pics, key=lambda p: p[0] != 3)[0]       # type 3 = front cover
    return data, ".png" if "png" in (mime or "").lower() else ".jpg"


def _save_cover(folder: str, data: bytes, ext: str = ".jpg") -> str | None:
    name = "cover" + ext
    try:
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(data)
        return name
    except OSError as exc:
        logger.info("Couldn't save the cover in %s: %s", folder, exc)
        return None


def ensure_cover(folder: str, files: list[str] | None = None, artist: str | None = None,
                 album: str | None = None, rg_mbid: str | None = None, online: bool = True) -> dict:
    """Give an album folder a cover image if it has none: from the files' embedded
    artwork first, else the Cover Art Archive (MusicBrainz). Never replaces a picture.
    Returns {"cover": file name or None, "source": "existing" | "embedded" | "musicbrainz" | None}."""
    have = cover_in(folder)
    if have:
        return {"cover": have, "source": "existing"}
    for f in (files if files is not None else audio_files(folder))[:5]:
        got = embedded_cover(f)
        if got:
            name = _save_cover(folder, *got)
            if name:
                return {"cover": name, "source": "embedded"}
            break
    if not online or not (rg_mbid or (artist and album)):
        return {"cover": None, "source": None}
    from . import musicbrainz
    try:
        rg = rg_mbid or musicbrainz.find_release_group(artist, album)
        data = musicbrainz.cover_bytes(rg) if rg else None
    except musicbrainz.MusicBrainzUnavailable:
        return {"cover": None, "source": None, "unavailable": True}
    if data:
        ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
        name = _save_cover(folder, data, ext)
        if name:
            return {"cover": name, "source": "musicbrainz"}
    return {"cover": None, "source": None}


def fill_covers(db, limit: int = SCAN_LOOKUPS) -> dict:
    """Covers for albums without one: embedded artwork for all of them, and up to
    ``limit`` MusicBrainz lookups (an album with none is asked again after 30 days)."""
    from datetime import timedelta
    from ..models import MusicLibAlbum
    now = datetime.now(timezone.utc)
    found = {"embedded": 0, "musicbrainz": 0}
    lookups = 0
    for a in db.query(MusicLibAlbum).filter(MusicLibAlbum.cover.is_(None)).all():
        if not os.path.isdir(a.folder):
            continue
        checked = a.cover_checked_at
        if checked is not None and checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        online = lookups < limit and (checked is None or now - checked > timedelta(days=COVER_RETRY_DAYS))
        res = ensure_cover(a.folder, None, a.artist, a.title, a.mb_release_group_id, online=online)
        if res.get("cover"):
            a.cover = res["cover"]
            found[res["source"]] = found.get(res["source"], 0) + 1
        elif online and not res.get("unavailable"):
            a.cover_checked_at = now
        if online and res.get("source") != "embedded":
            lookups += 1
    db.commit()
    return found


# ── editing by hand ─────────────────────────────────────────────────────────

ALBUM_FIELDS = ("album_artist", "album", "date", "genre", "comment")
_TAG_KEY = {"album_artist": "albumartist", "album": "album", "date": "date", "genre": "genre", "comment": "comment"}


def album_tags(files: list[str]) -> dict:
    """What the tag editor shows: album fields (the value all tracks share, else the
    most common, with ``mixed`` listing fields that differ) and one row per track."""
    from collections import Counter
    infos = _order([read(f) for f in files])
    album, mixed = {}, []
    for field in ALBUM_FIELDS:
        vals = [i[field] if field != "album_artist" else (i["album_artist"] or i["artist"]) for i in infos]
        counted = Counter(v for v in vals if v)
        album[field] = counted.most_common(1)[0][0] if counted else ""
        if len(set(vals)) > 1:
            mixed.append(field)
    tracks = [{"path": i["path"], "file": os.path.basename(i["path"]), "title": i["title"] or "",
               "artist": i["artist"] or "", "track_no": i["track_no"], "disc_no": i["disc_no"]} for i in infos]
    return {"album": album, "mixed": mixed, "tracks": tracks}


def live_title(date: str, venue: str, city: str) -> str:
    """Album title for a live recording: "2019-08-14 Red Rocks, Morrison CO"."""
    place = ", ".join(p for p in (venue.strip(), city.strip()) if p)
    return " ".join(p for p in (date.strip(), place) if p)


def save_album_tags(album: dict, tracks: list[dict], live: dict | None = None) -> dict:
    """Write the editor's values. ``album`` holds the shared fields (None = leave alone,
    "" = remove); ``tracks`` [{path, title, artist, track_no, disc_no}].
    ``live`` {date, venue, city} fills date, comment and (when blank) the album title.
    Track totals are worked out per disc. Returns {"ok", "saved", "failed"}."""
    album = dict(album or {})
    if live and any((live.get(k) or "").strip() for k in ("date", "venue", "city")):
        date, venue, city = ((live.get(k) or "").strip() for k in ("date", "venue", "city"))
        if not (album.get("album") or "").strip():
            album["album"] = live_title(date, venue, city)
        if date:
            album["date"] = date
        album["comment"] = "Live at " + live_title("", venue, city) + (f" on {date}" if date else "")
    discs = {t.get("disc_no") or 1 for t in tracks}
    per_disc: dict[int, int] = {}
    for t in tracks:
        per_disc[t.get("disc_no") or 1] = per_disc.get(t.get("disc_no") or 1, 0) + 1
    shared = {_TAG_KEY[k]: v for k, v in album.items() if k in _TAG_KEY}
    saved, failed = 0, []
    for t in tracks:
        values = dict(shared)
        if t.get("title") is not None:
            values["title"] = t["title"]
        if t.get("artist") is not None:
            values["artist"] = t["artist"] or album.get("album_artist") or ""
        if t.get("track_no"):
            values["tracknumber"] = f"{int(t['track_no'])}/{per_disc[t.get('disc_no') or 1]}"
        elif "track_no" in t:
            values["tracknumber"] = ""
        if len(discs) > 1 and t.get("disc_no"):
            values["discnumber"] = f"{int(t['disc_no'])}/{len(discs)}"
        if write(t["path"], values):
            saved += 1
        else:
            failed.append(os.path.basename(t["path"]))
    return {"ok": not failed, "saved": saved, "failed": failed}


_NAME_PATTERNS = [
    re.compile(r"^(?:.*?[^a-z])?d(?P<disc>\d{1,2})t(?P<no>\d{2,3})(?:[\s._-]+(?P<title>.+))?$", re.I),  # gd1977-05-08d2t01 (etree)
    re.compile(r"^(?:d|cd|disc)\s*(?P<disc>\d{1,2})\s*[-_. ]?\s*t?(?P<no>\d{1,3})[\s._-]+(?P<title>.+)$", re.I),  # d1t03 Title
    re.compile(r"^(?P<disc>\d)[-.](?P<no>\d{2})[\s._-]+(?P<title>.+)$"),                                 # 1-03 Title
    re.compile(r"^(?P<no>\d{1,3})\s*[-._)]\s*(?:(?P<artist>.+?)\s+-\s+)?(?P<title>.+)$"),                  # 03 - Title / 03. Artist - Title
    re.compile(r"^(?P<no>\d{1,3})\s+(?P<title>.+)$"),                                                       # 03 Title
    re.compile(r"^(?P<artist>.+?)\s+-\s+(?P<no>\d{1,3})\s+-\s+(?P<title>.+)$"),                              # Artist - 03 - Title
]


def from_name(filename: str) -> dict:
    """Title, track (and disc, artist) guessed from a file name like "03 - Song.flac"."""
    stem = os.path.splitext(os.path.basename(filename))[0].replace("_", " ").strip()
    for pat in _NAME_PATTERNS:
        m = pat.match(stem)
        if m:
            g = m.groupdict()
            title = re.sub(r"\s+", " ", (g.get("title") or "").strip(" -._")).strip()
            return {"title": title or None, "track_no": int(g["no"]),
                    "disc_no": int(g["disc"]) if g.get("disc") else None,
                    "artist": (g.get("artist") or "").strip() or None}
    return {"title": stem, "track_no": None, "disc_no": None, "artist": None}


# ── artwork by hand ─────────────────────────────────────────────────────────


def image_kind(data: bytes) -> str | None:
    """".jpg" or ".png" when ``data`` is a real image Pillow can open, else None."""
    import io
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
            fmt = (im.format or "").upper()
    except Exception:
        return None
    return {"JPEG": ".jpg", "PNG": ".png"}.get(fmt)


def replace_cover(folder: str, data: bytes) -> str:
    """Make ``data`` the album's cover (cover.jpg/png), removing the old cover image.
    Raises ValueError for anything that isn't a JPEG or PNG."""
    ext = image_kind(data)
    if ext is None:
        raise ValueError("That isn't a JPEG or PNG image")
    old = cover_in(folder)
    name = "cover" + ext
    tmp = os.path.join(folder, f".{name}.new")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, os.path.join(folder, name))
    if old and old != name and os.path.splitext(old)[0].lower() in IMAGE_NAMES:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return name


def embed_cover(files: list[str], data: bytes) -> dict:
    """Put the cover inside each audio file (FLAC, MP3, M4A, Ogg/Opus), replacing
    artwork already there. Returns {"embedded", "skipped"}."""
    import base64
    import mutagen
    from mutagen.flac import FLAC, Picture
    ext = image_kind(data)
    if ext is None:
        raise ValueError("That isn't a JPEG or PNG image")
    mime = "image/png" if ext == ".png" else "image/jpeg"
    done, skipped = 0, []
    for path in files:
        try:
            f = mutagen.File(path)
            if f is None:
                skipped.append(os.path.basename(path))
                continue
            if isinstance(f, FLAC):
                f.clear_pictures()
                pic = Picture()
                pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Cover", data
                f.add_picture(pic)
            elif path.lower().endswith(".mp3"):
                from mutagen.id3 import APIC, ID3, ID3NoHeaderError
                try:
                    tags = ID3(path)
                except ID3NoHeaderError:
                    tags = ID3()
                tags.delall("APIC")
                tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
                tags.save(path)
                done += 1
                continue
            elif path.lower().endswith((".m4a", ".mp4", ".aac", ".alac")):
                from mutagen.mp4 import MP4Cover
                fmt = MP4Cover.FORMAT_PNG if ext == ".png" else MP4Cover.FORMAT_JPEG
                f["covr"] = [MP4Cover(data, imageformat=fmt)]
            elif path.lower().endswith((".ogg", ".oga", ".opus")):
                pic = Picture()
                pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Cover", data
                f["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            else:
                skipped.append(os.path.basename(path))
                continue
            f.save()
            done += 1
        except Exception as exc:
            logger.warning("Couldn't embed the cover in %s: %s", path, exc)
            skipped.append(os.path.basename(path))
    return {"embedded": done, "skipped": skipped}


# ── organising ──────────────────────────────────────────────────────────────


def safe(part: str) -> str:
    from .organise import safe as _safe
    return _safe(part or "Unknown")


def target_folder(root: str, artist: str, album: str, year: int | None) -> str:
    name = f"{album} ({year})" if year else album
    return os.path.join(root, safe(artist), safe(name))


def track_name(info: dict, multi_disc: bool) -> str:
    ext = os.path.splitext(info["path"])[1]
    no = f"{info['track_no']:02d}" if info["track_no"] else None
    if no and multi_disc:
        no = f"{info['disc_no'] or 1}-{no}"
    title = info["title"] or os.path.splitext(os.path.basename(info["path"]))[0]
    return safe(f"{no} - {title}" if no else title) + ext


def _place(src: str, dest: str, keep_source: bool) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if keep_source:
        try:
            os.link(src, dest)                         # same disk: no extra space, still seeds
        except OSError:
            shutil.copy2(src, dest)
    else:
        shutil.move(src, dest)


SIDECAR_EXTS = {".cue", ".log", ".txt", ".nfo", ".m3u", ".m3u8", ".pdf", ".jpg", ".jpeg", ".png", ".gif"}


def organise(db, files: list[str], root: str, keep_source: bool, tidy: bool = True) -> dict:
    """Put an album's files in ``root/Artist/Album (Year)/NN - Title.ext`` with its cover.
    Never overwrites. When moving (not linking) and ``tidy``, the album's other files
    (.cue, .log, setlist .txt, scans …) go with it once no audio is left behind, and the
    emptied folder is removed. Returns {"ok", "folder", "moved", "skipped", "msg"}."""
    infos = _order([read(f) for f in files])
    if not infos:
        return {"ok": False, "msg": "No audio files"}
    first = infos[0]
    artist = next((i["album_artist"] for i in infos if i["album_artist"]), None)
    album = next((i["album"] for i in infos if i["album"]), None)
    if not artist or not album:
        return {"ok": False, "msg": "The files have no artist or album tags. Fix tags first."}
    year = next((i["year"] for i in infos if i["year"]), None)
    dest = target_folder(root, artist, album, year)
    multi = len({i["disc_no"] or 1 for i in infos}) > 1
    moved = skipped = 0
    for info in infos:
        to = os.path.join(dest, track_name(info, multi))
        if os.path.abspath(to) == os.path.abspath(info["path"]):
            continue
        if os.path.exists(to):
            skipped += 1
            continue
        _place(info["path"], to, keep_source)
        moved += 1
    src_dir = os.path.dirname(first["path"])
    pic = cover_in(src_dir)
    if pic and not cover_in(dest):
        try:
            _place(os.path.join(src_dir, pic), os.path.join(dest, "cover" + os.path.splitext(pic)[1].lower()),
                   keep_source)
        except OSError:
            pass
    if not keep_source and tidy and os.path.normpath(src_dir) != os.path.normpath(dest):
        left = os.listdir(src_dir) if os.path.isdir(src_dir) else []
        if not any(os.path.splitext(n)[1].lower() in AUDIO_EXTS for n in left):
            for n in left:
                p = os.path.join(src_dir, n)
                if n != pic and os.path.isfile(p) and os.path.splitext(n)[1].lower() in SIDECAR_EXTS \
                        and not os.path.exists(os.path.join(dest, n)):
                    shutil.move(p, os.path.join(dest, n))
        from .organise import _prune_empty
        _prune_empty(src_dir)
    return {"ok": True, "folder": dest, "moved": moved, "skipped": skipped,
            "msg": f"{moved} track{'s' if moved != 1 else ''} put in {dest}"
                   + (f" ({skipped} already there)" if skipped else "")}


def in_download_folder(db, folder: str) -> bool:
    """Is this inside a folder qBittorrent downloads to? (Then files are linked, not
    moved, so torrents keep seeding.)"""
    from ..models import DownloadPath
    from . import folders
    f = os.path.normpath(os.path.abspath(folder))
    for dp in db.query(DownloadPath).filter(DownloadPath.downloads.is_(True)).all():
        for r in {folders.local_of(db, dp), dp.local_path}:
            if r:
                r = os.path.normpath(os.path.abspath(r))
                if f == r or f.startswith(r.rstrip(os.sep) + os.sep):
                    return True
    return False


def music_dir(db) -> str | None:
    """The music library: the folder whose role is Music library (the old typed setting
    is only a fallback for installs that haven't moved over yet)."""
    from ..models import Setting
    from . import folder_roles
    found = folder_roles.path_for(db, "music")
    if found:
        return found
    row = db.query(Setting).filter(Setting.key == DIR_KEY).first()
    return row.value if row and row.value else None


# ── a finished album download ───────────────────────────────────────────────


def _retire_old_copy(db, lib_album_id: int) -> dict | None:
    """An upgrade arrived: move the old copy's audio files to the music trash first, so
    the new files never sit next to them. Returns what's needed to finish up."""
    from ..models import MusicLibAlbum, MusicTrack
    from . import music_doctor
    old = db.get(MusicLibAlbum, lib_album_id)
    if old is None or not os.path.isdir(old.folder):
        return None
    was = f"{(old.format or '?').upper()}, {old.tracks} track{'s' if old.tracks != 1 else ''}"
    rs = roots(db)
    for t in db.query(MusicTrack).filter(MusicTrack.album_id == old.id).all():
        if os.path.isfile(t.path):
            try:
                music_doctor.trash(t.path, rs)
            except OSError as exc:
                logger.warning("Couldn't move %s to the trash: %s", t.path, exc)
    folder = old.folder
    db.query(MusicTrack).filter(MusicTrack.album_id == old.id).delete()
    db.delete(old)
    db.commit()
    logger.info("Upgrade: old copy of %s (%s) moved to the trash", folder, was)
    return {"folder": folder, "was": was}


def _finish_replacing(db, replaced: dict, new_folder: str) -> None:
    """Bring the old copy's cover and notes (.cue/.log/.txt/scans) over when the new copy
    has none, then trash the old folder if nothing of it is left in use."""
    from . import music_doctor
    old = replaced["folder"]
    if not os.path.isdir(old) or os.path.normpath(old) == os.path.normpath(new_folder):
        return
    for n in os.listdir(old):
        p = os.path.join(old, n)
        if os.path.isfile(p) and os.path.splitext(n)[1].lower() in SIDECAR_EXTS \
                and not os.path.exists(os.path.join(new_folder, n)):
            if os.path.splitext(n)[1].lower() in IMAGE_EXTS and cover_in(new_folder):
                continue
            shutil.copy2(p, os.path.join(new_folder, n))
    if not audio_files(old) and old not in roots(db):
        try:
            music_doctor.trash(old, roots(db))
        except OSError as exc:
            logger.warning("Couldn't move %s to the trash: %s", old, exc)


def import_download(bind, download_id: int) -> dict:
    """Tag (if needed), organise into the music library and index a finished album."""
    from sqlalchemy.orm import Session
    from ..models import Download, MusicAlbum, WatchlistItem
    from .pipeline import map_path

    with Session(bind=bind) as db:
        dl = db.get(Download, download_id)
        if dl is None or not dl.music_album_id:
            return {"ok": False, "msg": "Not an album download"}
        album = db.get(MusicAlbum, dl.music_album_id)
        item = db.get(WatchlistItem, album.watchlist_id) if album else None
        remote = None
        if dl.torrent_hash and not dl.qbit_removed:
            try:
                from .qbittorrent import get_content_path
                remote = get_content_path(dl.torrent_hash)
            except Exception:
                remote = None
        local = map_path(db, remote or os.path.join(dl.download_path or "", dl.title))
        files = [local] if os.path.isfile(local) else audio_files(local) if os.path.isdir(local) else []
        files = [f for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTS]
        if not files:
            from . import music
            everything = [local] if os.path.isfile(local) else [
                os.path.join(h, n) for h, _, ns in os.walk(local) for n in ns] if os.path.isdir(local) else []
            why = music.not_music(everything)
            if why:
                music.reject_download(db, dl, why)
                if not dl.torrent_hash or os.path.exists(local):
                    _discard(db, local)
                return {"ok": False, "msg": why}
            return {"ok": False, "msg": f"No audio files found at {local}"}
        if item and album and any(not (i["title"] and i["album"] and i["track_no"]) for i in map(read, files)):
            try:
                fix_tags(files, item.title, album.title, album.mbid)
            except Exception as exc:
                logger.info("Tag fix for %s skipped: %s", album.title, exc)
        replaced = _retire_old_copy(db, dl.replaces_album_id) if dl.replaces_album_id else None
        root = music_dir(db)
        if root and os.path.isdir(root):
            res = organise(db, files, root, keep_source=True)
            folder = res.get("folder")
        else:
            res = {"ok": True, "msg": "No music library folder set; left in the download folder"}
            folder = os.path.dirname(files[0])
        if replaced and folder and os.path.isdir(folder):
            _finish_replacing(db, replaced, folder)
            res["msg"] = f"Replaced your {replaced['was']} copy. " + (res.get("msg") or "")
        if folder and os.path.isdir(folder):
            placed = audio_files(folder) if root and os.path.isdir(root) else files
            ensure_cover(folder, placed, item.title if item else None, album.title if album else None,
                         album.mbid if album else None)
            scan_folder(db, os.path.normpath(folder))
            mark_duplicates(db)
            db.commit()
        if res.get("ok") and folder and root and os.path.isdir(root):
            done = _clean_up_torrent(db, dl, local, files, folder)
            if done:
                res["msg"] = (res.get("msg") or "") + f". {done}"
            db.commit()
        logger.info("Album imported: %s", res.get("msg"))
    if res.get("ok"):
        refresh_plex_music(bind)
    return res


def _placed(src: str, folder: str) -> bool:
    """Is this downloaded track now in the library folder (linked or copied)?"""
    for n in os.listdir(folder) if os.path.isdir(folder) else []:
        p = os.path.join(folder, n)
        try:
            if os.path.samefile(src, p):
                return True
            if os.path.getsize(src) == os.path.getsize(p) \
                    and os.path.splitext(n)[1].lower() == os.path.splitext(src)[1].lower():
                return True
        except OSError:
            continue
    return False


def _clean_up_torrent(db, dl, local: str, files: list[str], folder: str) -> str | None:
    """Once every track is safely in the library, remove the torrent from qBittorrent and
    send its raw download to the music trash (30 days), so it isn't scanned as a second
    copy. Off with the music_remove_after_import setting."""
    from ..models import DownloadPath, MusicLibAlbum, MusicTrack, Setting
    from . import folders, music_doctor
    from .qbittorrent import remove_torrent
    row = db.query(Setting).filter(Setting.key == "music_remove_after_import").first()
    if row and (row.value or "").lower() == "false":
        return None
    src = os.path.normpath(local)
    if os.path.normpath(folder) == src or not all(_placed(f, folder) for f in files):
        return None
    rs = roots(db)
    keep = {os.path.normpath(r) for r in rs} | {
        os.path.normpath(p) for dp in db.query(DownloadPath).all()
        for p in (folders.local_of(db, dp), dp.local_path, dp.path) if p}
    targets = [src] if src not in keep else files            # never trash a whole root
    inside = all(music_doctor._root_of(t, rs) for t in targets)
    try:
        if dl.torrent_hash and not dl.qbit_removed:
            remove_torrent(dl.torrent_hash, delete_files=not inside)
            dl.qbit_removed = True
        elif not inside:
            return None                                     # already removed; leave its files
        if inside:
            for t in targets:
                if os.path.exists(t):
                    music_doctor.trash(t, rs)
            for a in db.query(MusicLibAlbum).filter(MusicLibAlbum.folder == src).all():
                db.query(MusicTrack).filter(MusicTrack.album_id == a.id).delete()
                db.delete(a)
    except Exception as exc:
        logger.warning("Couldn't clean up the torrent for %s: %s", dl.title, exc)
        return None
    logger.info("Removed %s from qBittorrent; its raw download is in the music trash", dl.title)
    return "Removed from qBittorrent"


def refresh_plex_music(bind) -> None:
    """Ask Plex to rescan its music libraries, so a new album shows up straight away."""
    from sqlalchemy.orm import Session
    from ..models import Setting
    from .media_servers import refresh_plex_sections
    try:
        with Session(bind=bind) as db:
            settings = {s.key: s.value for s in db.query(Setting).all()}
        if settings.get("plex_enabled", "false").lower() == "true" and settings.get("plex_url") \
                and settings.get("plex_token"):
            import asyncio
            asyncio.run(refresh_plex_sections(settings["plex_url"], settings["plex_token"], "artist"))
    except Exception as exc:
        logger.warning("Plex music refresh failed: %s", exc)


def tidy_finished(bind) -> int:
    """Hourly: finished album downloads still in qBittorrent are imported again (a no-op
    for tracks already placed) and cleaned up. Catches albums finished before clean-up
    existed, or while the library was unreachable."""
    from sqlalchemy.orm import Session
    from ..models import Download, MusicAlbum
    from . import music
    from .qbittorrent import file_names
    with Session(bind=bind) as db:
        for d in db.query(Download).filter(Download.music_album_id.isnot(None), Download.status == "downloading",
                                           Download.qbit_removed.is_(False), Download.torrent_hash.isnot(None)).all():
            why = music.not_music(file_names(d.torrent_hash))
            if why:
                music.reject_download(db, d, why)
        ids = [d.id for d in db.query(Download).join(MusicAlbum, MusicAlbum.id == Download.music_album_id)
               .filter(Download.status.in_(["completed", "seeding"]), Download.qbit_removed.is_(False),
                       MusicAlbum.status == "have").all()]
    n = 0
    for i in ids:
        try:
            if "Removed from qBittorrent" in (import_download(bind, i).get("msg") or ""):
                n += 1
        except Exception:
            logger.exception("Tidying album download %s failed", i)
    return n


def _discard(db, path: str) -> None:
    """Send a wrong download's files to the music trash when qBittorrent didn't delete them."""
    from . import music_doctor
    if not os.path.exists(path):
        return
    rs = roots(db)
    if music_doctor._root_of(path, rs) and os.path.normpath(path) not in {os.path.normpath(r) for r in rs}:
        try:
            music_doctor.trash(path, rs)
        except OSError as exc:
            logger.warning("Couldn't move %s to the trash: %s", path, exc)


def import_later(bind, download_id: int) -> None:
    def run():
        try:
            import_download(bind, download_id)
        except Exception:
            logger.exception("Album import failed (download %s)", download_id)
    if IMPORT_SYNC:
        run()
    else:
        threading.Thread(target=run, name=f"music-import-{download_id}", daemon=True).start()
