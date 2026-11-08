from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import link_vault
from ..services.indexers import search_all
from ..services.secret_settings import scrub_urls as scrub_text

router = APIRouter()


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    indexer: str = Query("all", description="Indexer: nyaa, tpb, jackett, eztv, yts, tgx, 1337x, or all"),
    quality: str = Query(None, description="Filter by quality: 4K, 1080p, 720p, 480p"),
    codec: str = Query("x265", description="Codec: x265, x264, AV1, or Any"),
    filter_adult: bool = Query(True, description="Filter out adult/pornographic content"),
    db: Session = Depends(get_db),
):
    try:
        results = search_all(
            q,
            quality=quality,
            indexer=indexer,
            codec=codec,
            filter_adult=filter_adult,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=scrub_text(str(e)))
    return link_vault.for_browser(db, results)
