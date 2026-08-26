from fastapi.testclient import TestClient

from app.db.session import Base, engine
import app.models  # noqa: F401 -- registers all tables on Base.metadata
from app.main import app

Base.metadata.create_all(bind=engine)

client = TestClient(app)


def test_health_returns_ok():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["ai_provider"] == "none"  # default local config -- no keys needed
    assert body["database_dialect"] == "sqlite"


def test_root_lists_docs_and_health():
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["docs"] == "/docs"
    assert body["health"] == "/api/health"


def test_all_eight_tables_registered():
    expected = {
        "customers",
        "payments",
        "payment_attempts",
        "recovery_cases",
        "ai_analyses",
        "recovery_actions",
        "audit_logs",
        "evaluation_results",
    }
    assert expected.issubset(set(Base.metadata.tables.keys()))
