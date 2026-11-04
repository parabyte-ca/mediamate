"""Keep adult content out of the Plex libraries.

Two rules, both enforced inside Rename & organise (services/organise.py), so no
path (Library, doctor, auto pipeline) can move such a file into a TV or Movies folder:

  1. Files in a **protected** folder (Settings → Folders; e.g. Bellesa) are never
     renamed or moved.
  2. Files that **look like adult content** — a studio/site name in the file name or
     any parent folder name — are never organised either, wherever they are.

Matching is on whole words, case-insensitive ("Vixen" matches "Vixen - Jojo",
"vixen.24.01.05" and "[VIXEN]", not "Vixenville"). Extra words can be added in the
`adult_keywords` setting (comma-separated).
"""
import os
import re

# Studios and sites (lower case, as words). Not exhaustive: protected folders are the
# main guard; this catches adult files that turn up anywhere else.
KEYWORDS = frozenset("""
bellesa bellesafilms bellesahouse vixen tushy tushyraw blacked blackedraw milfy hotwife
frolicme brazzers realitykings bangbros naughtyamerica teamskeet digitalplayground evilangel
kinkcom twistys metart metartx sexart x-art xart joymii passion-hd passionhd
playboyplus playboytv wickedpictures newsensations girlsway allgirlmassage puretaboo adulttime
nubiles nubilesporn spizoo privatecom letsdoeit sweetsinner mommysgirl fakehub fakeagent
fakehostel fakingstv publicagent castingcouchx backroomcastingcouch momxxx onlyfans
fansly manyvids clips4sale pornhub xvideos xhamster brattysis familystrokes exxxtrasmall
tiny4k povd babescom dorcel marcdorcel harmonyvision lustcinema
eroticax hegre femjoy ultrafilms wowgirls wowporn 21sextury 21naturals 21sextreme
""".split())
# Never listed: words that are also ordinary titles (Wicked, Private, Babes, The Hustler,
# Deeper, Passion…). Protected folders cover those files.
# Generic words that only ever mean adult content.
GENERIC = frozenset({"xxx", "porn", "porno", "nsfw", "hentai"})
_FOLDER_WORDS = ("bellesa", "adult", "xxx", "nsfw", "porn")
_WORD = re.compile(r"[a-z0-9+]+(?:-[a-z0-9]+)*")


def _words(text: str) -> set[str]:
    t = (text or "").lower().replace("_", " ").replace(".", " ")
    words = set(_WORD.findall(t))
    # "OnlyFans" and "Only Fans" both count: also try joined neighbours.
    seq = _WORD.findall(t)
    words |= {a + b for a, b in zip(seq, seq[1:])}
    return words


def extra_keywords(db=None) -> frozenset:
    """Words added in Settings (adult_keywords, comma-separated)."""
    try:
        from sqlalchemy.orm import Session
        from ..models import Setting
        from .media import store
        own = db is None
        db = db or Session(bind=store._engine())
        try:
            row = db.query(Setting).filter(Setting.key == "adult_keywords").first()
        finally:
            if own:
                db.close()
        raw = (row.value if row else "") or ""
    except Exception:
        raw = ""
    return frozenset(w.strip().lower() for w in raw.split(",") if w.strip())


def looks_adult(path: str, extra: frozenset = frozenset()) -> str | None:
    """Why this file looks like adult content, or None. Checks the file name and its
    parent folder names (not the whole absolute path's system part)."""
    parts = [p for p in os.path.normpath(path or "").split(os.sep) if p][-4:]   # file + 3 folders up
    known = KEYWORDS | GENERIC | extra
    for part in reversed(parts):
        stem = os.path.splitext(part)[0] if part == parts[-1] else part
        hit = sorted(_words(stem) & known)
        if hit:
            return f'"{hit[0]}" in {"the name" if part == parts[-1] else f"the folder {part}"}'
    return None


def guess_protected(name: str) -> bool:
    """Should a folder with this name be protected by default?"""
    n = (name or "").lower()
    return any(w in n for w in _FOLDER_WORDS)
