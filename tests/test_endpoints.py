"""Endpoint tests for the FastAPI app (selected)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app, clock, store
from app.models import AuditEntry, RecoveryCase


def test_verify_chain_endpoint_valid():
    case_id = f"pay_chain_endpoint_{uuid.uuid4().hex[:8]}"

    case = RecoveryCase(
        case_id=case_id,
        mandate_id=f"mandate_{case_id}",
        instrument_id=f"instr_{case_id}",
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


def test_explain_endpoint():
    case_id = f"pay_explain_{uuid.uuid4().hex[:8]}"
    case = RecoveryCase(
        case_id=case_id,
        mandate_id=f"mandate_{case_id}",
        instrument_id=f"instr_{case_id}",
        original_amount=10000,
        opened_at=clock.now(),
        state="RECOVERED",
    )
    store.create_case(case)
    client = TestClient(app)
    response = client.get(f"/cases/{case_id}/explain?question=Why")
    assert response.status_code == 200
    data = response.json()
    assert "explanation" in data