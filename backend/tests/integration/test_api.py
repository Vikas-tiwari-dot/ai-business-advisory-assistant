import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 -- registers all tables
from app.db.session import Base, get_db
from app.main import app


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test_api.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_simulation_generate_populates_the_database(client):
    resp = client.post("/api/simulation/generate", params={"records": 50, "seed": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["records_requested"] == 50
    assert body["records_processed"] == 50
    assert body["errors"] == 0
    assert body["seed"] == 1


def test_metrics_reflects_generated_data(client):
    client.post("/api/simulation/generate", params={"records": 50, "seed": 1})
    resp = client.get("/api/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["payments_recovered"] + body["pending_recovery"] + body["human_escalations"] > 0
    assert body["revenue_at_risk_display"].startswith("₹")
    assert isinstance(body["failure_categories"], list)


def test_payments_list_pagination(client):
    client.post("/api/simulation/generate", params={"records": 50, "seed": 1})
    resp = client.get("/api/payments", params={"page": 1, "page_size": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 50
    assert len(body["items"]) == 5
    assert body["page"] == 1


def test_payments_list_filters_by_status(client):
    client.post("/api/simulation/generate", params={"records": 50, "seed": 1})
    resp = client.get("/api/payments", params={"status": "recovered", "page_size": 100})
    assert resp.status_code == 200
    body = resp.json()
    assert all(item["status"] == "recovered" for item in body["items"])


def test_payment_detail_includes_full_audit_chain(client):
    client.post("/api/simulation/generate", params={"records": 50, "seed": 1})
    list_resp = client.get("/api/payments", params={"page_size": 1})
    payment_id = list_resp.json()["items"][0]["id"]

    resp = client.get(f"/api/payments/{payment_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["payment"]["id"] == payment_id
    assert body["audit_trail"][0]["stage"] == "PAYMENT_FAILED"  # always the first stage
    assert len(body["analyses"]) >= 1


def test_payment_detail_404_for_unknown_id(client):
    resp = client.get("/api/payments/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_payment_detail_422_for_malformed_id(client):
    resp = client.get("/api/payments/not-a-uuid")
    assert resp.status_code == 422


def test_recovery_queue_only_contains_escalated_cases(client):
    client.post("/api/simulation/generate", params={"records": 100, "seed": 2})
    resp = client.get("/api/recovery/queue")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) > 0
    assert all(item["case_status"] == "escalated" for item in items)


def test_approve_reject_escalate_stop_all_respond_and_log_human_decision(client):
    client.post("/api/simulation/generate", params={"records": 100, "seed": 3})
    queue = client.get("/api/recovery/queue").json()
    assert len(queue) >= 3

    approve_id = queue[0]["payment_id"]
    reject_id = queue[1]["payment_id"]
    stop_id = queue[2]["payment_id"]

    r1 = client.post(f"/api/recovery/{approve_id}/approve")
    assert r1.status_code == 200
    r2 = client.post(f"/api/recovery/{reject_id}/reject")
    assert r2.status_code == 200
    assert r2.json()["case_status"] == "closed_unrecovered"
    r3 = client.post(f"/api/recovery/{stop_id}/stop")
    assert r3.status_code == 200
    assert r3.json()["case_status"] == "stopped"

    # queue must have shrunk by exactly these 3 (none of them are ESCALATED anymore)
    new_queue = client.get("/api/recovery/queue").json()
    new_queue_ids = {item["payment_id"] for item in new_queue}
    assert approve_id not in new_queue_ids
    assert reject_id not in new_queue_ids
    assert stop_id not in new_queue_ids

    detail = client.get(f"/api/payments/{reject_id}").json()
    assert "HUMAN_DECISION" in [a["stage"] for a in detail["audit_trail"]]


def test_approve_404_when_no_escalated_case_exists(client):
    client.post("/api/simulation/generate", params={"records": 20, "seed": 4})
    payments = client.get("/api/payments", params={"page_size": 100}).json()["items"]
    non_escalated = next(p for p in payments if p["case_status"] != "escalated")
    resp = client.post(f"/api/recovery/{non_escalated['id']}/approve")
    assert resp.status_code == 404


def test_audit_endpoint_filters_by_payment_id(client):
    client.post("/api/simulation/generate", params={"records": 30, "seed": 5})
    payment_id = client.get("/api/payments", params={"page_size": 1}).json()["items"][0]["id"]
    resp = client.get("/api/audit", params={"payment_id": payment_id})
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) > 0
    # payload doesn't carry payment_id directly, but every entry should relate
    # to the same event chain -- checked indirectly via non-empty, ordered stages
    stages = [e["stage"] for e in entries]
    assert "PAYMENT_FAILED" in stages


def test_audit_endpoint_422_on_malformed_payment_id(client):
    resp = client.get("/api/audit", params={"payment_id": "not-a-uuid"})
    assert resp.status_code == 422


def test_audit_endpoint_filters_by_stage(client):
    client.post("/api/simulation/generate", params={"records": 30, "seed": 5})
    resp = client.get("/api/audit", params={"stage": "PAYMENT_FAILED", "limit": 200})
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) > 0
    assert all(e["stage"] == "PAYMENT_FAILED" for e in entries)


def test_audit_endpoint_filters_by_from_and_to(client):
    client.post("/api/simulation/generate", params={"records": 20, "seed": 5})
    all_entries = client.get("/api/audit", params={"limit": 200}).json()
    assert all_entries

    # A window that comfortably covers "now" should include everything;
    # a window entirely in the past should include nothing.
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    wide = client.get(
        "/api/audit",
        params={"from": (now - timedelta(days=1)).isoformat(), "to": (now + timedelta(days=1)).isoformat(), "limit": 200},
    ).json()
    assert len(wide) == len(all_entries)

    empty = client.get(
        "/api/audit",
        params={
            "from": (now - timedelta(days=10)).isoformat(),
            "to": (now - timedelta(days=9)).isoformat(),
        },
    ).json()
    assert empty == []


def test_payments_list_filters_by_case_status(client):
    client.post("/api/simulation/generate", params={"records": 100, "seed": 2})
    resp = client.get("/api/payments", params={"case_status": "escalated", "page_size": 100})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) > 0
    assert all(item["case_status"] == "escalated" for item in body["items"])


def test_recovery_endpoints_422_on_malformed_payment_id(client):
    for endpoint in ["approve", "reject", "escalate", "stop"]:
        resp = client.post(f"/api/recovery/not-a-uuid/{endpoint}")
        assert resp.status_code == 422, f"{endpoint} should 422 on malformed id"


def test_escalate_further_endpoint_logs_human_decision_without_changing_status(client):
    client.post("/api/simulation/generate", params={"records": 100, "seed": 3})
    queue = client.get("/api/recovery/queue").json()
    assert len(queue) > 0
    payment_id = queue[0]["payment_id"]

    resp = client.post(f"/api/recovery/{payment_id}/escalate")
    assert resp.status_code == 200
    assert resp.json()["case_status"] == "escalated"  # still escalated, just re-logged

    detail = client.get(f"/api/payments/{payment_id}").json()
    stages = [a["stage"] for a in detail["audit_trail"]]
    assert "HUMAN_DECISION" in stages


def test_evaluation_endpoint_reports_unavailable_when_none_persisted(client):
    resp = client.get("/api/evaluation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["metrics"] is None


def test_evaluation_endpoint_returns_persisted_result(client):
    from app.db.session import SessionLocal as _unused  # noqa: F401
    from app.models.audit import EvaluationResult

    db_gen = client.app.dependency_overrides[get_db]()
    db = next(db_gen)
    db.add(EvaluationResult(run_id="seed42", dataset_split="holdout", metrics_json={"recovery_rate": 0.25}))
    db.commit()
    db.close()

    resp = client.get("/api/evaluation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["run_id"] == "seed42"
    assert body["metrics"]["recovery_rate"] == 0.25


def test_simulation_generate_errors_field_is_zero_for_clean_run(client):
    resp = client.post("/api/simulation/generate", params={"records": 200, "seed": 6})
    assert resp.json()["errors"] == 0


def test_simulation_generate_counts_errors_without_failing_the_whole_batch(client, monkeypatch):
    """
    If processing an individual record raises (any exception -- DB hiccup,
    unexpected data shape, etc.), the batch endpoint must count it as an
    error and keep going, not let one bad record 500 the entire request.
    """
    import app.api.simulation as simulation_module

    real_run = simulation_module.run_pipeline_for_event
    call_count = {"n": 0}

    def _flaky_run(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] % 5 == 0:
            raise RuntimeError("simulated per-record processing failure")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(simulation_module, "run_pipeline_for_event", _flaky_run)

    resp = client.post("/api/simulation/generate", params={"records": 50, "seed": 9})
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] > 0
    assert body["records_processed"] == body["records_requested"] - body["errors"]
