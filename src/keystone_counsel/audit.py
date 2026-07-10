"""Hash-chained audit log for Keystone Counsel.

Same format and interface as keystone-engage. Platform consistency:
all Keystone extensions use the same audit chain format.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from keystone_counsel.models import AuditEntry

logger = logging.getLogger(__name__)


class AuditChain:
    """Append-only hash-chained audit ledger. JSONL backend."""

    def __init__(self, ledger_path: Path | str = "data/audit/ledger.jsonl") -> None:
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash = self._read_last_hash()

    def _read_last_hash(self) -> str:
        if not self.ledger_path.exists():
            return ""
        last_line = ""
        with open(self.ledger_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    last_line = line
        if not last_line:
            return ""
        try:
            entry = json.loads(last_line)
            return entry.get("curr_hash", "")
        except json.JSONDecodeError:
            logger.warning("Corrupt last line in audit ledger, starting new chain")
            return ""

    def append(
        self,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            actor=actor,
            payload=payload or {},
        )
        entry.compute_hash(self._last_hash)
        self._last_hash = entry.curr_hash

        with open(self.ledger_path, "a") as f:
            f.write(entry.model_dump_json() + "\n")

        logger.debug("Audit: %s by %s -> %s", event_type, actor, entry.curr_hash[:12])
        return entry

    def verify_chain(self) -> tuple[bool, int, str]:
        if not self.ledger_path.exists():
            return True, 0, "Empty ledger"

        prev_hash = ""
        count = 0
        with open(self.ledger_path) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    return False, count, f"Line {line_num}: invalid JSON"
                entry = AuditEntry(**data)
                stored_hash = entry.curr_hash
                entry.compute_hash(prev_hash)
                if entry.curr_hash != stored_hash:
                    return False, count, f"Line {line_num}: hash mismatch"
                if entry.prev_hash != prev_hash:
                    return False, count, f"Line {line_num}: prev_hash mismatch"
                prev_hash = stored_hash
                count += 1
        return True, count, f"Chain intact: {count} entries verified"
