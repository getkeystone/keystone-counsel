"""FastAPI application for Keystone Counsel.

Authorization-first retrieval for regulated content. On startup:
register demo advisors, choose vectorstore and audit backend based
on config, load and embed corpus, configure OTel.
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


def _create_vectorstore():
    """Choose vectorstore backend based on config."""
    settings = get_settings()
    if settings.database_url:
        try:
            from keystone_counsel.pgvectorstore import PgVectorStore
            store = PgVectorStore(settings.database_url)
            logger.info("Using PgVectorStore on Data-Plane")
            return store
        except Exception as e:
            logger.warning("PgVectorStore failed (%s), falling back to in-memory", e)
    logger.info("Using InMemoryVectorStore")
    return InMemoryVectorStore()


def _create_audit():
    """Choose audit backend based on config."""
    settings = get_settings()
    if settings.database_url:
        try:
            from keystone_counsel.pgaudit import PgAuditChain
            audit = PgAuditChain(settings.database_url)
            logger.info("Using PgAuditChain on Data-Plane")
            return audit
        except Exception as e:
            logger.warning("PgAuditChain failed (%s), falling back to JSONL", e)
    logger.info("Using JSONL AuditChain")
    return AuditChain()


async def _load_and_index_corpus(rag: CounselRAG, store_is_pg: bool) -> None:
    """Load corpus from classified directories and embed into vectorstore."""
    settings = get_settings()
    chunks = load_corpus(settings.corpus_dir)

    if not chunks:
        logger.warning("No corpus chunks loaded. RAG will operate in fail-closed mode.")
        return

    if store_is_pg and rag.vectorstore.size > 0:
        logger.info(
            "PgVectorStore already has %d chunks. Skipping re-embedding.",
            rag.vectorstore.size,
        )
        rag.mark_ready()
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

    vectorstore = _create_vectorstore()
    store_is_pg = not isinstance(vectorstore, InMemoryVectorStore)

    _audit = _create_audit()

    _rag = CounselRAG(vectorstore=vectorstore)
    await _load_and_index_corpus(_rag, store_is_pg)

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


# --- CORS (local lab / operator surface access) -------------------------------
# Default: an explicit allow-list of localhost origins so the private Platform
# Lab (served over http://localhost) can READ responses from a browser —
# cross-origin browser POSTs otherwise fail preflight. This is deliberately
# NOT permissive: no wildcard and no file:// (null) origin by default.
#
# Opt-in knobs (local demo only):
#   KEYSTONE_CORS_ORIGINS="http://localhost:5173,..."  → replace the allow-list
#   KEYSTONE_CORS_ORIGINS="*"                          → wildcard (discouraged)
#   KEYSTONE_CORS_ALLOW_FILE="1"                       → also allow "null" (file://)
#
# Auth is Bearer-token in the Authorization header (no cookies), so
# allow_credentials stays False.
import os
from fastapi.middleware.cors import CORSMiddleware

_cors_env = os.environ.get("KEYSTONE_CORS_ORIGINS", "").strip()
if _cors_env == "*":
    _cors_origins = ["*"]
elif _cors_env:
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    _cors_origins = [
        "http://localhost:8082", "http://127.0.0.1:8082",
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:5500", "http://127.0.0.1:5500",
    ]
# file:// (null) origin is opt-in only — keep it out of the default policy.
if os.environ.get("KEYSTONE_CORS_ALLOW_FILE", "").strip() in ("1", "true", "True") \
        and "*" not in _cors_origins and "null" not in _cors_origins:
    _cors_origins = _cors_origins + ["null"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        agent_id='counsel-agent-v1',
        tempo='medium',
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
                agent_id="counsel-agent-v1",
                tempo="medium",
            )
            return CounselResponse(
                query=request.query,
                answer="Invalid document classification requested.",
                severity=SeverityTier.TIER_3,
                audit_hash=opening.curr_hash,
                fail_closed=True,
            )
    else:
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
            agent_id="counsel-agent-v1",
            tempo="medium",
        )
        if authz.allowed:
            authorized_classifications.append(classification)
        else:
            denied_classifications.append(classification.value)

    if not authorized_classifications:
        _audit.append(
            event_type="authorization.denied_all",
            actor="counsel-orchestrator",
            payload={
                "advisor_id": request.advisor_id,
                "denied": denied_classifications,
            },
            agent_id="counsel-agent-v1",
            tempo="medium",
        )
        return CounselResponse(
            query=request.query,
            answer="Not authorized to access any requested document classifications.",
            severity=SeverityTier.TIER_3,
            authorization=authz,
            audit_hash=opening.curr_hash,
            fail_closed=True,
        )

    # 4. Retrieve and generate via RAG (classification- and client-filtered).
    #    request.client_id scopes retrieval to global content plus this client's
    #    rows; other clients' chunks are excluded at the retrieval layer.
    rag_response = await _rag.retrieve_and_generate(
        query=request.query,
        allowed_classifications=[c.value for c in authorized_classifications],
        client_id=request.client_id,
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
        agent_id="counsel-agent-v1",
        tempo="medium",
        input_tokens=rag_response.input_tokens,
        output_tokens=rag_response.output_tokens,
        model_used=rag_response.model_used,
        cost_cents=0,
        latency_ms=round(rag_response.latency_ms),
    )

    return CounselResponse(
        query=request.query,
        answer=answer,
        severity=severity,
        citations=citations,
        audit_hash=closing.curr_hash,
        fail_closed=rag_response.fail_closed,
    )
