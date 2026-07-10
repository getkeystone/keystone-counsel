"""Baseline tests for Keystone Counsel scaffold.

Tests the authorization matrix (the core differentiator), API endpoints,
and audit chain integrity. 12 tests covering authorization rules, API
smoke, and audit chain.
"""

import pytest
from fastapi.testclient import TestClient

from keystone_counsel.api import app
from keystone_counsel.audit import AuditChain
from keystone_counsel.auth import authorize_retrieval, get_advisor_store
from keystone_counsel.models import (
    AdvisorProfile,
    AdvisorRole,
    DocumentClassification,
)


@pytest.fixture(autouse=True)
def _setup_advisors():
    """Register test advisors before each test."""
    store = get_advisor_store()
    store.advisors.clear()
    store.register(AdvisorProfile(
        advisor_id="test-suit",
        role=AdvisorRole.SUITABILITY_ADVISOR,
        client_ids=["client-A", "client-B"],
    ))
    store.register(AdvisorProfile(
        advisor_id="test-compliance",
        role=AdvisorRole.COMPLIANCE_OFFICER,
        client_ids=[],
    ))
    store.register(AdvisorProfile(
        advisor_id="test-assoc",
        role=AdvisorRole.ASSOCIATE_COUNSEL,
        client_ids=["client-A"],
    ))
    store.register(AdvisorProfile(
        advisor_id="test-senior",
        role=AdvisorRole.SENIOR_COUNSEL,
        client_ids=["client-A", "client-B"],
    ))


# --- Authorization matrix tests ---


class TestAuthorizationMatrix:
    """Test the relational authorization model: role + client + classification."""

    def test_suitability_advisor_can_access_regulatory_guidance(self):
        result = authorize_retrieval("test-suit", DocumentClassification.REGULATORY_GUIDANCE)
        assert result.allowed

    def test_suitability_advisor_can_access_own_client_kyc(self):
        result = authorize_retrieval(
            "test-suit", DocumentClassification.KYC_DOCUMENT, client_id="client-A"
        )
        assert result.allowed

    def test_suitability_advisor_denied_other_client_kyc(self):
        result = authorize_retrieval(
            "test-suit", DocumentClassification.KYC_DOCUMENT, client_id="client-Z"
        )
        assert not result.allowed
        assert "not authorized for client" in result.reason

    def test_suitability_advisor_denied_legal_opinion(self):
        result = authorize_retrieval(
            "test-suit", DocumentClassification.LEGAL_OPINION, client_id="client-A"
        )
        assert not result.allowed
        assert "denied access" in result.reason

    def test_compliance_officer_can_access_any_client_kyc(self):
        result = authorize_retrieval(
            "test-compliance", DocumentClassification.KYC_DOCUMENT, client_id="client-Z"
        )
        assert result.allowed

    def test_compliance_officer_denied_privileged(self):
        result = authorize_retrieval(
            "test-compliance", DocumentClassification.PRIVILEGED, client_id="client-A"
        )
        assert not result.allowed

    def test_associate_counsel_own_client_legal_opinion(self):
        result = authorize_retrieval(
            "test-assoc", DocumentClassification.LEGAL_OPINION, client_id="client-A"
        )
        assert result.allowed

    def test_associate_counsel_denied_other_client_legal_opinion(self):
        result = authorize_retrieval(
            "test-assoc", DocumentClassification.LEGAL_OPINION, client_id="client-B"
        )
        assert not result.allowed

    def test_senior_counsel_can_access_own_client_privileged(self):
        result = authorize_retrieval(
            "test-senior", DocumentClassification.PRIVILEGED, client_id="client-A"
        )
        assert result.allowed

    def test_senior_counsel_denied_other_client_privileged(self):
        result = authorize_retrieval(
            "test-senior", DocumentClassification.PRIVILEGED, client_id="client-Z"
        )
        assert not result.allowed

    def test_unknown_advisor_denied(self):
        result = authorize_retrieval("nobody", DocumentClassification.REGULATORY_GUIDANCE)
        assert not result.allowed
        assert "not registered" in result.reason

    def test_own_client_requires_client_id(self):
        """Suitability advisor asking for KYC without specifying a client."""
        result = authorize_retrieval(
            "test-suit", DocumentClassification.KYC_DOCUMENT, client_id=None
        )
        assert not result.allowed
        assert "requires a client_id" in result.reason


# --- API smoke tests ---


class TestAPI:
    @pytest.fixture
    def client(self):
        with TestClient(app) as c:
            yield c

    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["component"] == "keystone-counsel"
        assert data["platform"] == "keystone"

    def test_counsel_authorized_request(self, client):
        response = client.post("/counsel", json={
            "query": "What are the suitability requirements?",
            "advisor_id": "test-suit",
            "client_id": "client-A",
        })
        assert response.status_code == 200
        data = response.json()
        assert "authorized" in data["answer"].lower() or "scaffold" in data["answer"].lower()

    def test_counsel_denied_request(self, client):
        response = client.post("/counsel", json={
            "query": "Show me the privileged memo",
            "advisor_id": "test-suit",
            "client_id": "client-A",
            "classification_filter": ["privileged"],
        })
        assert response.status_code == 200
        data = response.json()
        assert data["severity"] == "tier_3"
        assert data["fail_closed"] is True


# --- Audit chain tests ---


class TestAuditChain:
    def test_audit_chain_integrity(self, tmp_path):
        chain = AuditChain(ledger_path=tmp_path / "test.jsonl")
        chain.append("test.event", "tester", {"key": "value1"})
        chain.append("test.event", "tester", {"key": "value2"})
        chain.append("test.event", "tester", {"key": "value3"})
        valid, count, msg = chain.verify_chain()
        assert valid
        assert count == 3
