"""FastAPI application for Keystone Counsel.

Authorization-first retrieval for regulated content. On startup:
register demo advisors, initialize audit chain, configure OTel.
RAG pipeline is a stub in this scaffold; wired to Ollama in the
next commit.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from keystone_counsel import __version__
from keystone_counsel.audit import AuditChain
from keystone_counsel.auth import authorize_retrieval, get_advisor_store
from keystone_counsel.config import get_settings
from keystone_counsel.models import (
    AdvisorProfile,
    AdvisorRole,
    AuthorizationResult,
    CounselRequest,
    CounselResponse,
    DocumentClassification,
    HealthResponse,
    SeverityTier,
)
from keystone_counsel.observability import setup_telemetry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_audit: AuditChain | None = None


def _register_demo_advisors() -> None:
    """Register demo advisor profiles for development and testing."""
    store = get_advisor_store()
    store.register(AdvisorProfile(
        advisor_id="advisor-001",
        role=AdvisorRole.SUITABILITY_ADVISOR,
        client_ids=["client-A", "client-B"],
        display_name="Demo Suitability Advisor",
    ))
    store.register(AdvisorProfile(
        advisor_id="compliance-001",
        role=AdvisorRole.COMPLIANCE_OFFICER,
        client_ids=[],
        display_name="Demo Compliance Officer",
    ))
    store.register(AdvisorProfile(
        advisor_id="counsel-001",
        role=AdvisorRole.ASSOCIATE_COUNSEL,
        client_ids=["client-A", "client-C"],
        display_name="Demo Associate Counsel",
    ))
    store.register(AdvisorProfile(
        advisor_id="senior-001",
        role=AdvisorRole.SENIOR_COUNSEL,
        client_ids=["client-A", "client-B", "client-C"],
        display_name="Demo Senior Counsel",
    ))
    logger.info("Registered 4 demo advisor profiles")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _audit

    _register_demo_advisors()
    _audit = AuditChain()

    logger.info("Keystone Counsel v%s ready", __version__)
    yield
    logger.info("Keystone Counsel shutting down")


app = FastAPI(
    title="Keystone Counsel",
    description=(
        "Regulated content RAG for legal and financial advisory. "
        "Authorization-first retrieval with advisor role, client relationship, "
        "and document classification as first-class dimensions. "
        "Part of the Keystone Applied Intelligence platform."
    ),
    version=__version__,
    lifespan=lifespan,
)

_tracer = setup_telemetry(app)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@app.post("/counsel", response_model=CounselResponse)
async def counsel(request: CounselRequest) -> CounselResponse:
    """Authorization-first retrieval endpoint.

    Pipeline:
    1. Resolve advisor profile
    2. Determine document classifications to search
    3. Authorize each classification (fail-closed on any denial)
    4. Retrieve from authorized corpus (stub in scaffold)
    5. Generate response with citations (stub in scaffold)
    6. Audit the full decision chain
    """
    assert _audit is not None, "Audit chain not initialized"

    # 1. Audit open
    opening = _audit.append(
        event_type="request.received",
        actor="counsel-orchestrator",
        payload={
            "advisor_id": request.advisor_id,
            "client_id": request.client_id or "",
            "query_length": len(request.query),
            "jurisdiction": request.jurisdiction or "",
        },
    )

    # 2. Determine classifications to check
    if request.classification_filter:
        try:
            classifications = [
                DocumentClassification(c) for c in request.classification_filter
            ]
        except ValueError as e:
            _audit.append(
                event_type="authorization.invalid_classification",
                actor="counsel-orchestrator",
                payload={"error": str(e)},
            )
            return CounselResponse(
                query=request.query,
                answer="Invalid document classification requested.",
                severity=SeverityTier.TIER_3,
                audit_hash=opening.curr_hash,
                fail_closed=True,
            )
    else:
        # Default: check all non-privileged classifications
        classifications = [
            DocumentClassification.REGULATORY_GUIDANCE,
            DocumentClassification.SUITABILITY_ASSESSMENT,
            DocumentClassification.KYC_DOCUMENT,
            DocumentClassification.LEGAL_OPINION,
        ]

    # 3. Authorize each classification
    authorized_classifications: list[DocumentClassification] = []
    denied_classifications: list[str] = []

    for classification in classifications:
        authz = authorize_retrieval(
            advisor_id=request.advisor_id,
            classification=classification,
            client_id=request.client_id,
            agent_identity="counsel-agent-v1",
        )
        _audit.append(
            event_type="authorization.checked",
            actor="counsel-orchestrator",
            payload={
                "classification": classification.value,
                "allowed": authz.allowed,
                "reason": authz.reason,
            },
        )
        if authz.allowed:
            authorized_classifications.append(classification)
        else:
            denied_classifications.append(classification.value)

    # Fail-closed: if no classifications are authorized, deny entirely
    if not authorized_classifications:
        _audit.append(
            event_type="authorization.denied_all",
            actor="counsel-orchestrator",
            payload={
                "advisor_id": request.advisor_id,
                "denied": denied_classifications,
            },
        )
        return CounselResponse(
            query=request.query,
            answer="Not authorized to access any requested document classifications.",
            severity=SeverityTier.TIER_3,
            authorization=authz,
            audit_hash=opening.curr_hash,
            fail_closed=True,
        )

    # 4. Retrieve (stub: fail-closed until RAG is wired)
    # In the next commit, this calls the RAG pipeline with
    # authorized_classifications as the ACL filter.
    _audit.append(
        event_type="retrieval.stub",
        actor="counsel-orchestrator",
        payload={
            "authorized_classifications": [c.value for c in authorized_classifications],
            "denied_classifications": denied_classifications,
            "status": "stub_fail_closed",
        },
    )

    closing = _audit.append(
        event_type="response.generated",
        actor="counsel-orchestrator",
        payload={
            "severity": SeverityTier.TIER_2.value,
            "fail_closed": True,
            "reason": "RAG pipeline not yet wired (scaffold mode)",
            "authorized_count": len(authorized_classifications),
        },
    )

    return CounselResponse(
        query=request.query,
        answer=(
            f"Retrieval authorized for {len(authorized_classifications)} classification(s). "
            f"RAG pipeline not yet wired. This is the scaffold response."
        ),
        severity=SeverityTier.TIER_2,
        citations=[],
        audit_hash=closing.curr_hash,
        fail_closed=True,
    )
