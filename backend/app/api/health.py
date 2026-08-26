from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # pragma: no cover - defensive
        db_status = f"error: {exc}"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "app_name": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "ai_provider": settings.ai_provider,
        "db": db_status,
        "database_dialect": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql",
    }
