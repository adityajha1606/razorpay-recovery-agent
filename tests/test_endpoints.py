"""Endpoint tests for the FastAPI app (selected)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app, clock, store
from app.models import AuditEntry, RecoveryCase


def test_verify_chain_endpoint_valid():
    # Seed store with a case and chained audit entries
    case = RecoveryCase(
        case_id="pay_chain_endpoint",
        mandate_id="mandate_chain_endpoint",
        instrument_id="instr_chain_endpoint",
        original_amount=10000,
        opened_at=clock.now(),
        state="RECOVERED",
    )
    store.create_case(case)

    entry1 = AuditEntry(
        case_id=case.case_id,
        from_state="RECEIVED",
        to_state="CLASSIFIED",
        rule_fired="create_case",
        rule_version=2,
        timestamp=clock.now(),
        actor="agent",
    )
    store.append_audit(entry1)

    entry2 = AuditEntry(
        case_id=case.case_id,
        from_state="CLASSIFIED",
        to_state="TREATMENT",
        rule_fired="assign_bucket_treatment",
        rule_version=2,
        timestamp=clock.now(),
        actor="agent",
    )
    store.append_audit(entry2)

    client = TestClient(app)
    response = client.get(f"/cases/{case.case_id}/verify_chain")
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["detail"] == "Chain intact"


def test_verify_chain_endpoint_empty_case_returns_false():
    client = TestClient(app)
    response = client.get("/cases/nonexistent/verify_chain")
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False