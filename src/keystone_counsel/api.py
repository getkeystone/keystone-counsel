"""FastAPI application for Keystone Counsel.

Authorization-first retrieval for regulated content. On startup:
register demo advisors, load and embed corpus, initialize audit chain.
RAG pipeline wired to Ollama on ZenithForge.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from keystone_counsel import __version__
from keystone_counsel.audit import AuditChain
from keystone_counsel.auth import authorize_retrieval, get_advisor_store
from keystone_counsel.config import get_settings
from keystone_counsel.corpus import load_corpus
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
from keystone_counsel.rag import CounselRAG
from keystone_counsel.vectorstore import InMemoryVectorStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_audit: AuditChain | None = None
_rag: CounselRAG | None = None


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


async def _load_and_index_corpus(rag: CounselRAG) -> None:
    """Load corpus from classified directories and embed into vectorstore."""
    settings = get_settings()
    chunks = load_corpus(settings.corpus_dir)

    if not chunks:
        logger.warning("No corpus chunks loaded. RAG will operate in fail-closed mode.")
        return

    logger.info("Embedding %d chunks (this may take a moment)...", len(chunks))
    try:
        texts = [c.content for c in chunks]
        embeddings = await rag.embed_batch(texts)
        for chunk, embedding in zip(chunks, embeddings):
            rag.vectorstore.add(chunk, embedding)
        rag.mark_ready()
        logger.info(
            "Corpus indexed: %d chunks across %d classifications. RAG is ready.",
            rag.vectorstore.size,
            len(set(c.classification for c in chunks)),
        )
    except Exception as e:
        logger.warning(
            "Failed to embed corpus (Ollama unreachable?): %s. "
            "RAG will operate in fail-closed mode.",
            e,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _audit, _rag

    _register_demo_advisors()
    _audit = AuditChain()

    vectorstore = InMemoryVectorStore()
    _rag = CounselRAG(vectorstore=vectorstore)
    await _load_and_index_corpus(_rag)

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
    1. Audit open
    2. Determine document classifications to search
    3. Authorize each classification (fail-closed on any denial)
    4. Retrieve from authorized corpus via RAG
    5. Generate response with citations
    6. Audit close
    """
    assert _audit is not None, "Audit chain not initialized"
    assert _rag is not None, "RAG pipeline not initialized"

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

    # 4. Retrieve and generate via RAG (classification-filtered)
    rag_response = await _rag.retrieve_and_generate(
        query=request.query,
        allowed_classifications=[c.value for c in authorized_classifications],
    )

    # 5. Build response
    if rag_response.fail_closed:
        severity = SeverityTier.TIER_2
        answer = (
            "Unable to retrieve a confident response from authorized documents. "
            "Please consult the relevant regulatory authority or legal counsel."
        )
    else:
        severity = SeverityTier.TIER_0
        answer = rag_response.answer

    citations = [
        {
            "chunk_id": c.chunk_id,
            "source": c.source_document,
            "section": c.section,
            "classification": c.classification,
            "score": c.similarity_score,
        }
        for c in rag_response.retrieved_chunks
    ]

    # 6. Audit close
    closing = _audit.append(
        event_type="response.generated",
        actor="counsel-orchestrator",
        payload={
            "severity": severity.value,
            "fail_closed": rag_response.fail_closed,
            "fail_reason": rag_response.fail_reason,
            "model_used": rag_response.model_used,
            "confidence": rag_response.confidence_score,
            "authorized_classifications": [c.value for c in authorized_classifications],
            "denied_classifications": denied_classifications,
            "chunk_count": len(rag_response.retrieved_chunks),
            "input_tokens": rag_response.input_tokens,
            "output_tokens": rag_response.output_tokens,
            "latency_ms": round(rag_response.latency_ms, 1),
        },
    )

    return CounselResponse(
        query=request.query,
        answer=answer,
        severity=severity,
        citations=citations,
        audit_hash=closing.curr_hash,
        fail_closed=rag_response.fail_closed,
    )
