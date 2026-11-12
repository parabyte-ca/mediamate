"""Small album covers for the music library.

Cover files are often full-size scans (several MB). Lists show them at 40 px, so
each size the app uses gets a small JPEG, made once and cached under
<data dir>/cache/covers, keyed by album, size and the cover's modification time
(a new cover gives a new file).
"""
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SIZES = (96, 160, 320, 512)
QUALITY = 82
_slots = threading.BoundedSemaphore(4)


def cache_dir() -> Path:
    from .media import preview
    return preview.cache_dir().parent / "covers"


def thumb(album_id: int, src: str, size: int) -> str:
    """Path to a JPEG of the cover at most size×size; the original when it can't be made."""
    if size not in SIZES:
        raise ValueError("size")
    try:
        mtime = int(os.stat(src).st_mtime)
    except OSError:
        return src
    out = cache_dir() / f"{album_id}-{size}-{mtime}.jpg"
    if out.is_file() and out.stat().st_size > 0:
        return str(out)
    with _slots:
        if out.is_file() and out.stat().st_size > 0:      # made while we waited
            return str(out)
        try:
            from PIL import Image, ImageOps
            out.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(src) as im:
                if im.format == "JPEG":
                    im.draft("RGB", (size * 2, size * 2))      # decode at reduced scale: much faster
                im = ImageOps.exif_transpose(im)
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im, mask=im.getchannel("A"))
                    im = bg
                im.thumbnail((size, size))
                tmp = out.with_suffix(".tmp")
                im.convert("RGB").save(tmp, "JPEG", quality=QUALITY, progressive=True, optimize=True)
                os.replace(tmp, out)
            return str(out)
        except Exception as exc:                            # a broken image: send what we have
            logger.warning("Couldn't make a %spx cover for album %s: %s", size, album_id, exc)
            return src


def prune(days: int = 60) -> int:
    folder, n = cache_dir(), 0
    if not folder.is_dir():
        return 0
    cut = time.time() - days * 86400
    for f in folder.glob("*.jpg"):
        try:
            if f.stat().st_atime < cut and f.stat().st_mtime < cut:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n
