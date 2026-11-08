"""People in the household, each with their own view of one shared watchlist.

* A show or movie is stored and downloaded once; people **follow** it. When two people
  follow it, both see it; when one stops, it stays for the other.
* No logins: each phone or browser remembers who is using it and sends ``X-Person``.
* Phone alerts about a show go to the devices of the people following it; system
  alerts (updates, disk space, organise reports) go to the owner's devices. Devices
  not yet linked to anyone get everything, as before."""
from sqlalchemy.orm import Session

from ..models import Person, PushSubscription, WatchlistFollower, WatchlistItem

COLORS = ["#2563eb", "#db2777", "#16a34a", "#d97706", "#7c3aed", "#0891b2"]
HEADER = "x-person"


def ensure_owner(db: Session) -> Person:
    """The first person (the owner) exists, and follows anything nobody follows yet
    (everything, on the first start after the upgrade)."""
    owner = db.query(Person).filter(Person.is_owner.is_(True)).first()
    if owner is None:
        owner = db.query(Person).order_by(Person.id).first()
        if owner is None:
            owner = Person(name="Me", color=COLORS[0], is_owner=True)
            db.add(owner)
        owner.is_owner = True
        db.flush()
    followed = {w for (w,) in db.query(WatchlistFollower.watchlist_id).distinct()}
    for (wid,) in db.query(WatchlistItem.id):
        if wid not in followed:
            db.add(WatchlistFollower(person_id=owner.id, watchlist_id=wid))
    db.commit()
    return owner


def owner(db: Session) -> Person:
    return db.query(Person).filter(Person.is_owner.is_(True)).first() or ensure_owner(db)


def current(db: Session, request) -> Person | None:
    """Who this request is for (the X-Person header), or None if unknown."""
    raw = request.headers.get(HEADER) if request is not None else None
    try:
        return db.get(Person, int(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def acting(db: Session, request) -> Person:
    """The person a new watchlist item is added for: this device's, else the owner."""
    return current(db, request) or owner(db)


def followers(db: Session, item_ids=None) -> dict[int, list[int]]:
    q = db.query(WatchlistFollower)
    if item_ids is not None:
        q = q.filter(WatchlistFollower.watchlist_id.in_(list(item_ids)))
    out: dict[int, list[int]] = {}
    for f in q.order_by(WatchlistFollower.person_id):
        out.setdefault(f.watchlist_id, []).append(f.person_id)
    return out


def followed_by(db: Session, person_id: int) -> set[int]:
    return {w for (w,) in db.query(WatchlistFollower.watchlist_id).filter(WatchlistFollower.person_id == person_id)}


def follow(db: Session, watchlist_id: int, person_id: int) -> bool:
    """True when this added a follower (False: already following)."""
    exists = db.query(WatchlistFollower).filter_by(watchlist_id=watchlist_id, person_id=person_id).first()
    if exists:
        return False
    db.add(WatchlistFollower(watchlist_id=watchlist_id, person_id=person_id))
    db.commit()
    return True


def unfollow(db: Session, watchlist_id: int, person_id: int) -> int:
    """Stop following; returns how many people still follow it."""
    db.query(WatchlistFollower).filter_by(watchlist_id=watchlist_id, person_id=person_id).delete()
    db.commit()
    return db.query(WatchlistFollower).filter_by(watchlist_id=watchlist_id).count()


def remove(db: Session, person: Person) -> None:
    """Remove someone (never the owner): what only they followed passes to the owner,
    and their devices go back to getting every alert."""
    if person.is_owner:
        raise ValueError("The owner can't be removed")
    boss = owner(db)
    for wid in followed_by(db, person.id):
        if db.query(WatchlistFollower).filter(WatchlistFollower.watchlist_id == wid,
                                              WatchlistFollower.person_id != person.id).count() == 0:
            db.add(WatchlistFollower(watchlist_id=wid, person_id=boss.id))
    db.query(WatchlistFollower).filter(WatchlistFollower.person_id == person.id).delete()
    db.query(PushSubscription).filter(PushSubscription.person_id == person.id).update({PushSubscription.person_id: None})
    db.delete(person)
    db.commit()


def recipients(db: Session, watchlist_id: int | None) -> set[int] | None:
    """Whose devices get an alert: the item's followers, or the owner for system
    alerts. None means every device (nobody set up yet)."""
    if db.query(Person.id).count() <= 1:
        return None                                          # one person: nothing to split
    if watchlist_id is None:
        return {owner(db).id}
    who = {p for (p,) in db.query(WatchlistFollower.person_id).filter(WatchlistFollower.watchlist_id == watchlist_id)}
    return who or {owner(db).id}


def out(p: Person) -> dict:
    url = None
    if p.avatar and p.avatar_at:
        url = f"/api/people/{p.id}/avatar?v={int(p.avatar_at.timestamp())}"
    return {"id": p.id, "name": p.name, "color": p.color or COLORS[(p.id - 1) % len(COLORS)],
            "is_owner": bool(p.is_owner), "avatar_url": url}


MAX_AVATAR = 5 * 1024 * 1024
AVATAR_SIZE = 256


def make_avatar(data: bytes) -> bytes:
    """A 256×256 JPEG: upright (phone photos carry their rotation in EXIF), centre
    cropped to a square. Raises ValueError for anything that isn't an image."""
    import io
    from PIL import Image, ImageOps, UnidentifiedImageError
    if len(data) > MAX_AVATAR:
        raise ValueError("That picture is over 5 MB")
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError):
        raise ValueError("That isn't a picture MediaMate can read. Use a JPG or PNG.")
    img = ImageOps.fit(img.convert("RGB"), (AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()
