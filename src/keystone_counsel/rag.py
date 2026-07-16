"""Retrieval and generation for Keystone Counsel.

Same fail-closed pattern as Engage. Key difference: retrieval filters
by authorized document classifications before similarity search. The
system prompt requires explicit citation of source documents.

Contact center heritage: this is the knowledge base lookup with
queue-based access control. Only articles in authorized queues are
eligible for delivery to the advisor.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from openai import AsyncOpenAI

from keystone_counsel.config import get_settings
from keystone_counsel.vectorstore import InMemoryVectorStore, QueryResult

logger = logging.getLogger(__name__)

COUNSEL_SYSTEM_PROMPT = """You are a regulated content retrieval assistant for legal and financial advisory. You help advisors find and understand regulatory guidance, suitability requirements, KYC obligations, and legal opinions.

RULES:
1. Only use information from the PROVIDED CONTEXT below. Do not invent or assume facts.
2. If the context does not contain enough information to answer the query, say "The available documents do not contain sufficient information to answer this query" and recommend the advisor consult the relevant regulatory authority or legal counsel.
3. For every factual claim, cite the source document name and section in brackets, e.g. [Source: suitability-requirements.md, Section: Documentation Requirements].
4. Do not provide legal advice. Present the regulatory text and its implications, but recommend professional consultation for specific situations.
5. When regulatory requirements include specific thresholds, timelines, or percentages, state them exactly as documented. Do not round, approximate, or paraphrase numerical requirements.
6. If multiple documents address the query, synthesize across sources and cite each one.
7. Keep responses precise and structured. Regulatory content requires clarity, not conversational warmth.

CONTEXT:
{context}

Respond to the advisor's query based on the rules and context above."""


@dataclass
class RetrievalResult:
    chunk_id: str
    content: str
    source_document: str
    section: str
    classification: str
    similarity_score: float
    evidence_tier: str = "verified"


@dataclass
class RAGResponse:
    """Response from the RAG pipeline with full provenance."""

    answer: str
    retrieved_chunks: list[RetrievalResult]
    model_used: str
    confidence_score: float
    fail_closed: bool = False
    fail_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class CounselRAG:
    """RAG pipeline for Keystone Counsel.

    Classification-aware retrieval: only chunks from authorized
    classifications are eligible for similarity search. Fail-closed
    when confidence is below threshold.
    """

    def __init__(
        self,
        vectorstore: InMemoryVectorStore | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        settings = get_settings()
        self.vectorstore = vectorstore or InMemoryVectorStore()
        self.client = client or AsyncOpenAI(
            base_url=f"{settings.ollama_base_url}/v1",
            api_key="ollama",
        )
        self.chat_model = settings.ollama_chat_model
        self.embed_model = settings.ollama_embed_model
        self.top_k = settings.retrieval_top_k
        self.confidence_threshold = settings.confidence_threshold
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready and self.vectorstore.size > 0

    def mark_ready(self) -> None:
        self._ready = True

    async def embed(self, text: str) -> list[float]:
        response = await self.client.embeddings.create(
            model=self.embed_model,
            input=text,
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        embeddings = []
        for text in texts:
            emb = await self.embed(text)
            embeddings.append(emb)
        return embeddings

    async def retrieve_and_generate(
        self,
        query: str,
        allowed_classifications: list[str] | None = None,
        client_id: str | None = None,
        max_chunks: int | None = None,
    ) -> RAGResponse:
        """Retrieve relevant chunks and generate a governed response.

        allowed_classifications: only chunks from these classifications
        are eligible. This is the classification authorization-at-retrieval gate.
        client_id: the client context of this request. Global chunks (client_id
        None) are eligible for any caller; client-specific chunks are eligible
        only for the matching client. When None, only global content is
        returned (fail-closed for client-specific data). The caller (api.py)
        determines both dimensions from the advisor's role and client
        relationship.
        """
        top_k = max_chunks or self.top_k
        start_time = time.monotonic()

        if not self.ready:
            return RAGResponse(
                answer="",
                retrieved_chunks=[],
                model_used=self.chat_model,
                confidence_score=0.0,
                fail_closed=True,
                fail_reason="RAG pipeline not ready: corpus not loaded or inference unavailable",
            )

        try:
            query_embedding = await self.embed(query)
        except Exception as e:
            logger.error("Embedding failed: %s", e)
            return RAGResponse(
                answer="", retrieved_chunks=[], model_used=self.chat_model,
                confidence_score=0.0, fail_closed=True,
                fail_reason=f"Embedding failed: {e}",
            )

        results: list[QueryResult] = self.vectorstore.query(
            query_embedding,
            k=top_k,
            allowed_classifications=allowed_classifications,
            caller_client_id=client_id,
        )

        if not results:
            elapsed = (time.monotonic() - start_time) * 1000
            return RAGResponse(
                answer="", retrieved_chunks=[], model_used=self.chat_model,
                confidence_score=0.0, fail_closed=True,
                fail_reason="No chunks retrieved for authorized classifications",
                latency_ms=elapsed,
            )

        best_score = results[0].score
        retrieved = [
            RetrievalResult(
                chunk_id=r.chunk.chunk_id,
                content=r.chunk.content,
                source_document=r.chunk.source_document,
                section=r.chunk.section,
                classification=r.chunk.classification,
                similarity_score=r.score,
                evidence_tier=r.chunk.evidence_tier,
            )
            for r in results
        ]

        if best_score < self.confidence_threshold:
            elapsed = (time.monotonic() - start_time) * 1000
            return RAGResponse(
                answer="", retrieved_chunks=retrieved, model_used=self.chat_model,
                confidence_score=best_score, fail_closed=True,
                fail_reason=f"Best retrieval score {best_score:.3f} below threshold {self.confidence_threshold}",
                latency_ms=elapsed,
            )

        context_parts = []
        for r in results:
            context_parts.append(
                f"[Source: {r.chunk.source_document}, Section: {r.chunk.section}, "
                f"Classification: {r.chunk.classification}]\n{r.chunk.content}"
            )
        context = "\n\n---\n\n".join(context_parts)

        try:
            completion = await self.client.chat.completions.create(
                model=self.chat_model,
                messages=[
                    {"role": "system", "content": COUNSEL_SYSTEM_PROMPT.format(context=context)},
                    {"role": "user", "content": query},
                ],
                temperature=0.2,
            )
            answer = completion.choices[0].message.content or ""
            input_tokens = completion.usage.prompt_tokens if completion.usage else 0
            output_tokens = completion.usage.completion_tokens if completion.usage else 0
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            elapsed = (time.monotonic() - start_time) * 1000
            return RAGResponse(
                answer="", retrieved_chunks=retrieved, model_used=self.chat_model,
                confidence_score=best_score, fail_closed=True,
                fail_reason=f"LLM call failed: {e}", latency_ms=elapsed,
            )

        elapsed = (time.monotonic() - start_time) * 1000
        return RAGResponse(
            answer=answer, retrieved_chunks=retrieved, model_used=self.chat_model,
            confidence_score=best_score, input_tokens=input_tokens,
            output_tokens=output_tokens, latency_ms=elapsed,
        )
