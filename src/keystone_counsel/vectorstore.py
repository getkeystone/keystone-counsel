"""Vectorstore for Keystone Counsel.

Classification-aware: chunks carry a document classification, and queries
filter by a set of authorized classifications before similarity search.
This is the in-memory backend. PgVectorStore (Phase 2) applies the same
filter as a WHERE clause on the pgvector query.

Contact center heritage: this is queue-based routing. The query enters
a pool, but only agents (chunks) in authorized queues (classifications)
are eligible to handle it.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A corpus chunk with classification metadata."""

    chunk_id: str
    content: str
    source_document: str
    section: str
    classification: str  # regulatory_guidance, suitability_assessment, etc.
    evidence_tier: str = "verified"
    jurisdiction: str | None = None
    client_id: str | None = None


@dataclass
class QueryResult:
    chunk: Chunk
    score: float


class InMemoryVectorStore:
    """In-memory vectorstore with classification filtering."""

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._embeddings: list[list[float]] = []

    @property
    def size(self) -> int:
        return len(self._chunks)

    def add(self, chunk: Chunk, embedding: list[float]) -> None:
        self._chunks.append(chunk)
        self._embeddings.append(embedding)

    def query(
        self,
        query_embedding: list[float],
        k: int = 5,
        allowed_classifications: list[str] | None = None,
    ) -> list[QueryResult]:
        """Query with optional classification filtering.

        If allowed_classifications is provided, only chunks with a matching
        classification are eligible. This is the authorization gate at the
        retrieval layer: denied classifications never appear in results.
        """
        if not self._chunks:
            return []

        scored: list[tuple[int, float]] = []
        for i, emb in enumerate(self._embeddings):
            # Classification filter
            if allowed_classifications is not None:
                if self._chunks[i].classification not in allowed_classifications:
                    continue
            score = self._cosine_similarity(query_embedding, emb)
            scored.append((i, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            QueryResult(chunk=self._chunks[i], score=score)
            for i, score in scored[:k]
        ]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
