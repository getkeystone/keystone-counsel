"""PostgreSQL-backed audit chain for Keystone Counsel.

Same interface as AuditChain (JSONL). Hash-chained, append-only, tamper-evident.
Writes substrate columns (agent_id, tempo, task_id, cost fields) alongside payload.
Platform-consistent with keystone-engage.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

import psycopg2
import psycopg2.extras

from keystone_counsel.models import AuditEntry

logger = logging.getLogger(__name__)


class PgAuditChain:
    """Append-only hash-chained audit ledger backed by PostgreSQL."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._last_hash = self._read_last_hash()
        logger.info("PgAuditChain: connected to Data-Plane")

    @contextmanager
    def _conn(self) -> Generator:
        conn = psycopg2.connect(self._database_url)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _read_last_hash(self) -> str:
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT curr_hash FROM audit_entries ORDER BY id DESC LIMIT 1"
                    )
                    row = cur.fetchone()
                    return row[0] if row else ""
        except Exception as e:
            logger.warning("Could not read last audit hash: %s", e)
            return ""

    def append(
        self,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        agent_id: str | None = None,
        tempo: str | None = None,
        task_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        model_used: str | None = None,
        cost_cents: float | None = None,
        latency_ms: int | None = None,
        session_rolling_cost_cents: float | None = None,
    ) -> AuditEntry:
        """Append a hash-chained entry with optional substrate columns."""
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            actor=actor,
            payload=payload or {},
        )
        entry.compute_hash(self._last_hash)
        self._last_hash = entry.curr_hash

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO audit_entries
                       (timestamp, event_type, actor, payload, prev_hash, curr_hash,
                        agent_id, tempo, task_id,
                        input_tokens, output_tokens, model_used,
                        cost_cents, latency_ms, session_rolling_cost_cents)
                       VALUES (%s, %s, %s, %s, %s, %s,
                               %s, %s, %s,
                               %s, %s, %s,
                               %s, %s, %s)""",
                    (
                        entry.timestamp,
                        entry.event_type,
                        entry.actor,
                        json.dumps(entry.payload),
                        entry.prev_hash,
                        entry.curr_hash,
                        agent_id,
                        tempo,
                        task_id,
                        input_tokens,
                        output_tokens,
                        model_used,
                        cost_cents,
                        latency_ms,
                        session_rolling_cost_cents,
                    ),
                )

        logger.debug("PgAudit: %s by %s -> %s", event_type, actor, entry.curr_hash[:12])
        return entry

    def verify_chain(self) -> tuple[bool, int, str]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, event_type, actor, payload, prev_hash, curr_hash "
                    "FROM audit_entries ORDER BY id ASC"
                )
                rows = cur.fetchall()

        if not rows:
            return True, 0, "Empty ledger"

        prev_hash = ""
        for i, row in enumerate(rows):
            entry = AuditEntry(
                timestamp=row[0],
                event_type=row[1],
                actor=row[2],
                payload=row[3] if isinstance(row[3], dict) else json.loads(row[3]),
                prev_hash=row[4],
                curr_hash=row[5],
            )
            stored_hash = entry.curr_hash
            entry.compute_hash(prev_hash)
            if entry.curr_hash != stored_hash:
                return False, i, f"Entry {i+1}: hash mismatch"
            prev_hash = stored_hash

        return True, len(rows), f"Chain intact: {len(rows)} entries verified"
