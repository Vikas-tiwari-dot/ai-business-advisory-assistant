from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.audit import router as audit_router
from app.api.evaluation import router as evaluation_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.api.payments import router as payments_router
from app.api.recovery import router as recovery_router
from app.api.simulation import router as simulation_router
from app.core.config import get_settings
from app.core.exceptions import RazorRecoverError, razorrecover_exception_handler
from app.core.logging import configure_logging
from app.db.session import Base, engine

settings = get_settings()
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Local-dev convenience: create any missing tables on boot so a bare
    # `uvicorn app.main:app --reload` against a fresh SQLite file works with
    # zero manual steps. Safe to run every startup -- create_all() only adds
    # tables that don't already exist, never alters or drops existing ones.
    # Production/Docker deployments still use `alembic upgrade head` as the
    # source of truth for schema changes (see backend/Dockerfile); this is a
    # convenience floor under that, not a replacement for it.
    import app.models  # noqa: F401 -- registers all tables on Base.metadata

    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "AI revenue recovery platform. AI decides, policy controls, system executes, "
        "audit records, metrics prove. No real money or real customer data is ever used."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(RazorRecoverError, razorrecover_exception_handler)

app.include_router(health_router, prefix="/api")
app.include_router(events_router, prefix="/api")
app.include_router(simulation_router, prefix="/api")
app.include_router(payments_router, prefix="/api")
app.include_router(recovery_router, prefix="/api")
app.include_router(audit_router, prefix="/api")
app.include_router(metrics_router, prefix="/api")
app.include_router(evaluation_router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {"service": settings.app_name, "docs": "/docs", "health": "/api/health"}
