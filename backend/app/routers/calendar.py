from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.calendar_view import build_calendar

router = APIRouter()


@router.get("/calendar")
async def get_calendar(
    request: Request,
    days: int = Query(30, ge=1, le=120, description="Days ahead to include"),
    back: int = Query(7, ge=0, le=30, description="Days back to include"),
    who: str = Query("mine", description="mine (this device's person) or all"),
    db: Session = Depends(get_db),
):
    """Upcoming (and just-aired) episodes of the watchlisted shows you follow (or everyone's)."""
    from ..services import people
    only = None
    me = people.current(db, request)
    if who != "all" and me is not None:
        people.ensure_owner(db)
        only = people.followed_by(db, me.id)
    return await build_calendar(db, days_back=back, days_ahead=days, only=only)
