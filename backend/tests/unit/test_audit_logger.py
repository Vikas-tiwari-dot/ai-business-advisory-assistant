import ast
import inspect
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.customer import Customer
from app.models.payment import Payment
from app.services.audit import logger as logger_module
from app.services.audit.logger import AuditLogger


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def payment(db_session):
    customer = Customer(external_customer_id="cust_test")
    db_session.add(customer)
    db_session.flush()
    p = Payment(
        customer_id=customer.id,
        razorpay_payment_id="pay_test",
        amount=100000,
        currency="INR",
        payment_method="card",
        status="failed",
    )
    db_session.add(p)
    db_session.flush()
    return p


# --- Structural guard: this module must never UPDATE or DELETE AuditLog ----


def test_audit_logger_module_has_no_update_or_delete_statements():
    """
    Phase 9 exit criterion: append-only enforced at the repository layer.
    Scans the actual source of app/services/audit/logger.py for any mutation
    verb against AuditLog rows.
    """
    source = inspect.getsource(logger_module)
    forbidden_terms = [".update(", ".delete(", "DELETE FROM", "UPDATE audit_logs", "db.delete", "session.delete"]
    for term in forbidden_terms:
        assert term not in source, f"AuditLogger source must not contain {term!r}"


def test_audit_logger_class_exposes_no_mutation_methods():
    """Belt-and-suspenders: enumerate the class's own methods and confirm none look like a mutator."""
    methods = [name for name in dir(AuditLogger) if not name.startswith("_")]
    assert set(methods) == {"log", "get_trail", "event_already_seen"}
    forbidden_method_names = {"update", "update_entry", "delete", "delete_entry", "edit", "modify"}
    assert forbidden_method_names.isdisjoint(methods)


def test_audit_logger_has_no_sqlalchemy_update_delete_ast_calls():
    """AST-level check: no ast.Attribute access named 'update' or 'delete' anywhere in the module."""
    tree = ast.parse(inspect.getsource(logger_module))
    attribute_names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "update" not in attribute_names
    assert "delete" not in attribute_names


# --- Functional behavior ------------------------------------------------------


def test_log_persists_a_row_and_returns_it(db_session, payment):
    audit = AuditLogger(db_session)
    entry = audit.log(
        event_id="evt_1",
        payment_id=payment.id,
        stage="PAYMENT_FAILED",
        payload={"status": "failed"},
    )
    db_session.commit()

    assert entry.id is not None
    assert entry.event_id == "evt_1"
    assert entry.stage == "PAYMENT_FAILED"
    assert entry.payload_json == {"status": "failed"}
    assert entry.system_version  # populated from settings by default


def test_get_trail_returns_rows_in_chronological_order(db_session, payment):
    audit = AuditLogger(db_session)
    audit.log(event_id="evt_1", payment_id=payment.id, stage="PAYMENT_FAILED", payload={})
    audit.log(event_id="evt_1", payment_id=payment.id, stage="RISK_DETECTED", payload={})
    audit.log(event_id="evt_1", payment_id=payment.id, stage="AI_DIAGNOSED", payload={})
    db_session.commit()

    trail = audit.get_trail(payment.id)
    stages = [entry.stage for entry in trail]
    assert stages == ["PAYMENT_FAILED", "RISK_DETECTED", "AI_DIAGNOSED"]


def test_get_trail_only_returns_rows_for_the_given_payment(db_session):
    customer = Customer(external_customer_id="cust_a")
    db_session.add(customer)
    db_session.flush()
    payment_a = Payment(customer_id=customer.id, razorpay_payment_id="pay_a", amount=1000, payment_method="card", status="failed")
    payment_b = Payment(customer_id=customer.id, razorpay_payment_id="pay_b", amount=2000, payment_method="card", status="failed")
    db_session.add_all([payment_a, payment_b])
    db_session.flush()

    audit = AuditLogger(db_session)
    audit.log(event_id="evt_a", payment_id=payment_a.id, stage="PAYMENT_FAILED", payload={})
    audit.log(event_id="evt_b", payment_id=payment_b.id, stage="PAYMENT_FAILED", payload={})
    db_session.commit()

    trail_a = audit.get_trail(payment_a.id)
    assert len(trail_a) == 1
    assert trail_a[0].event_id == "evt_a"


def test_event_already_seen_detects_duplicates(db_session, payment):
    audit = AuditLogger(db_session)
    assert audit.event_already_seen("evt_1") is False
    audit.log(event_id="evt_1", payment_id=payment.id, stage="PAYMENT_FAILED", payload={})
    db_session.commit()
    assert audit.event_already_seen("evt_1") is True


def test_event_already_seen_is_false_for_a_different_event_id(db_session, payment):
    audit = AuditLogger(db_session)
    audit.log(event_id="evt_1", payment_id=payment.id, stage="PAYMENT_FAILED", payload={})
    db_session.commit()
    assert audit.event_already_seen("evt_2") is False


def test_log_never_reuses_or_mutates_an_existing_row(db_session, payment):
    audit = AuditLogger(db_session)
    entry1 = audit.log(event_id="evt_1", payment_id=payment.id, stage="PAYMENT_FAILED", payload={"n": 1})
    entry2 = audit.log(event_id="evt_1", payment_id=payment.id, stage="RISK_DETECTED", payload={"n": 2})
    db_session.commit()

    assert entry1.id != entry2.id  # two distinct rows, not one row updated twice
    trail = audit.get_trail(payment.id)
    assert len(trail) == 2
