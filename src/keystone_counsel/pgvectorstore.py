"""PostgreSQL-backed vectorstore for Keystone Counsel.

Classification-aware ACL filtering at the database level. The WHERE clause
on classification fires before the vector similarity search, so denied
classifications never appear in results regardless of similarity score.

Contact center heritage: this is queue-based routing enforced at the
database layer. A query can only match chunks in authorized queues.
No application-level filtering required (defense in depth: the app
filters too, but the DB is the primary gate).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

import numpy as np
import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector

from keystone_counsel.vectorstore import Chunk, QueryResult

logger = logging.getLogger(__name__)


class PgVectorStore:
    """pgvector-backed store with classification filtering."""

    def __init__(self, database_url: str, embedding_dim: int = 768) -> None:
        self._database_url = database_url
        self._embedding_dim = embedding_dim
        self._verify_table()

    @contextmanager
    def _conn(self) -> Generator:
        conn = psycopg2.connect(self._database_url)
        register_vector(conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _verify_table(self) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_name = 'chunks'"
                )
                if cur.fetchone()[0] == 0:
                    raise RuntimeError("chunks table not found. Run migration first.")
        logger.info("PgVectorStore: table verified on AnchorNode")

    @property
    def size(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM chunks")
                return cur.fetchone()[0]

    def add(self, chunk: Chunk, embedding: list[float]) -> None:
        """Insert a chunk with its embedding. Upserts on chunk_id."""
        emb_array = np.array(embedding, dtype=np.float32)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO chunks
                       (chunk_id, content, source_document, section,
                        classification, evidence_tier, jurisdiction, client_id, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (chunk_id) DO UPDATE SET
                           content = EXCLUDED.content,
                           embedding = EXCLUDED.embedding""",
                    (
                        chunk.chunk_id,
                        chunk.content,
                        chunk.source_document,
                        chunk.section,
                        chunk.classification,
                        chunk.evidence_tier,
                        chunk.jurisdiction,
                        chunk.client_id,
                        emb_array,
                    ),
                )

    def query(
        self,
        query_embedding: list[float],
        k: int = 5,
        allowed_classifications: list[str] | None = None,
    ) -> list[QueryResult]:
        """Classification-filtered similarity search.

        The WHERE clause on classification is the ACL gate. It fires
        before the vector similarity operator, so denied classifications
        are excluded at the database level. This is not application-level
        filtering; the database enforces the boundary.
        """
        emb_array = np.array(query_embedding, dtype=np.float32)

        if allowed_classifications:
            sql = """
                SELECT chunk_id, content, source_document, section,
                       classification, evidence_tier, jurisdiction, client_id,
                       1 - (embedding <=> %s) AS similarity
                FROM chunks
                WHERE classification = ANY(%s)
                ORDER BY embedding <=> %s
                LIMIT %s
            """
            params = (emb_array, allowed_classifications, emb_array, k)
        else:
            sql = """
                SELECT chunk_id, content, source_document, section,
                       classification, evidence_tier, jurisdiction, client_id,
                       1 - (embedding <=> %s) AS similarity
                FROM chunks
                ORDER BY embedding <=> %s
                LIMIT %s
            """
            params = (emb_array, emb_array, k)

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        results = []
        for row in rows:
            chunk = Chunk(
                chunk_id=row[0],
                content=row[1],
                source_document=row[2],
                section=row[3],
                classification=row[4],
                evidence_tier=row[5],
                jurisdiction=row[6],
                client_id=row[7],
            )
            results.append(QueryResult(chunk=chunk, score=row[8]))

        return results
