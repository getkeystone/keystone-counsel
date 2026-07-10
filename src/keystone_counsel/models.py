"""Pydantic types for Keystone Counsel.

The authorization model is the core differentiator from Engage.
Engage has flat scope: caller role maps to corpus. Counsel has
relational scope: advisor role + client relationship + document
classification determines access.

Contact center heritage: this is the skill-based routing matrix
from workforce management. An agent (advisor) has skills (role),
assigned queues (clients), and the routing engine checks all three
dimensions before delivering the interaction (document).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# --- Advisor roles ---


class AdvisorRole(str, Enum):
    """Role determines the base authorization scope.

    suitability_advisor : can access regulatory guidance and own-client
                          suitability/KYC documents.
    compliance_officer  : can access regulatory guidance and any-client
                          suitability/KYC documents. Cannot access legal
                          opinions or privileged documents.
    associate_counsel   : can access regulatory guidance and own-client
                          legal opinions. Cannot access privileged documents.
    senior_counsel      : can access everything including own-client
                          privileged documents.
    """

    SUITABILITY_ADVISOR = "suitability_advisor"
    COMPLIANCE_OFFICER = "compliance_officer"
    ASSOCIATE_COUNSEL = "associate_counsel"
    SENIOR_COUNSEL = "senior_counsel"


# --- Document classification ---


class DocumentClassification(str, Enum):
    """Classification determines the sensitivity tier of a document.

    regulatory_guidance     : publicly available regulatory text. Any role.
    suitability_assessment  : client-specific suitability analysis.
    kyc_document            : client-specific KYC/AML documentation.
    legal_opinion           : legal analysis, may reference client matters.
    privileged              : attorney-client privileged material.
    """

    REGULATORY_GUIDANCE = "regulatory_guidance"
    SUITABILITY_ASSESSMENT = "suitability_assessment"
    KYC_DOCUMENT = "kyc_document"
    LEGAL_OPINION = "legal_opinion"
    PRIVILEGED = "privileged"


# --- Advisor profile ---


class AdvisorProfile(BaseModel):
    """An advisor with role and client relationships.

    The client_ids list is the set of clients this advisor is authorized
    to access documents for. An empty list means no client-specific access.
    """

    advisor_id: str
    role: AdvisorRole
    client_ids: list[str] = Field(default_factory=list)
    display_name: str = ""


# --- Authorization result ---


class AuthorizationResult(BaseModel):
    """Result of an authorization check. Logged to the audit chain."""

    allowed: bool
    reason: str
    decision_source: str = "in-process"
    advisor_id: str = ""
    agent_identity: str = ""
    requested_classification: str = ""
    requested_client_id: str = ""


# --- Severity tiers ---


class SeverityTier(str, Enum):
    """HITL routing severity. Same tiers as Engage for platform consistency."""

    TIER_0 = "tier_0"
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"


# --- Audit entry (hash-chained, same format as keystone-core and Engage) ---


class AuditEntry(BaseModel):
    """Hash-chained audit record. Append-only, tamper-evident."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str = ""
    curr_hash: str = ""

    def compute_hash(self, prev_hash: str) -> str:
        self.prev_hash = prev_hash
        content = json.dumps(
            {
                "timestamp": self.timestamp.isoformat(),
                "event_type": self.event_type,
                "actor": self.actor,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
        )
        self.curr_hash = hashlib.sha256(content.encode()).hexdigest()
        return self.curr_hash


# --- API types ---


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    component: str = "keystone-counsel"
    platform: str = "keystone"


class CounselRequest(BaseModel):
    """Inbound retrieval request to the Counsel agent."""

    query: str
    advisor_id: str
    client_id: str | None = None
    jurisdiction: str | None = None
    classification_filter: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CounselResponse(BaseModel):
    """Outbound response from the Counsel agent."""

    query: str
    answer: str
    severity: SeverityTier = SeverityTier.TIER_0
    citations: list[dict[str, Any]] = Field(default_factory=list)
    authorization: AuthorizationResult | None = None
    audit_hash: str = ""
    fail_closed: bool = False
