"""Phase 1 authorization for Keystone Counsel.

Relational authorization: advisor role + client relationship + document
classification determines access. This is structurally richer than Engage's
flat scope model and is the primary reason OPA ships in Phase 2.

Authorization rules (v1):

  suitability_advisor:
    - regulatory_guidance    : any client (public material)
    - suitability_assessment : own clients only
    - kyc_document           : own clients only
    - legal_opinion          : DENIED
    - privileged             : DENIED

  compliance_officer:
    - regulatory_guidance    : any client
    - suitability_assessment : any client
    - kyc_document           : any client
    - legal_opinion          : DENIED
    - privileged             : DENIED

  associate_counsel:
    - regulatory_guidance    : any client
    - suitability_assessment : DENIED
    - kyc_document           : DENIED
    - legal_opinion          : own clients only
    - privileged             : DENIED

  senior_counsel:
    - regulatory_guidance    : any client
    - suitability_assessment : any client
    - kyc_document           : any client
    - legal_opinion          : any client
    - privileged             : own clients only

Contact center heritage: this is the skill-based routing matrix.
An agent has skills (role), assigned queues (clients), and the
routing engine checks all three before delivering the interaction.

MIGRATION: This module is replaced by OPA in graduation path Stage 1.1.
The interface (authorize_retrieval) stays the same; the backend changes.
See docs/MIGRATION.md for the input tuple and externalization plan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from keystone_counsel.models import (
    AdvisorProfile,
    AdvisorRole,
    AuthorizationResult,
    DocumentClassification,
)

logger = logging.getLogger(__name__)


# Role-classification access matrix.
# True = any client, "own" = own clients only, False = denied.
_ACCESS_MATRIX: dict[AdvisorRole, dict[DocumentClassification, bool | str]] = {
    AdvisorRole.SUITABILITY_ADVISOR: {
        DocumentClassification.REGULATORY_GUIDANCE: True,
        DocumentClassification.SUITABILITY_ASSESSMENT: "own",
        DocumentClassification.KYC_DOCUMENT: "own",
        DocumentClassification.LEGAL_OPINION: False,
        DocumentClassification.PRIVILEGED: False,
    },
    AdvisorRole.COMPLIANCE_OFFICER: {
        DocumentClassification.REGULATORY_GUIDANCE: True,
        DocumentClassification.SUITABILITY_ASSESSMENT: True,
        DocumentClassification.KYC_DOCUMENT: True,
        DocumentClassification.LEGAL_OPINION: False,
        DocumentClassification.PRIVILEGED: False,
    },
    AdvisorRole.ASSOCIATE_COUNSEL: {
        DocumentClassification.REGULATORY_GUIDANCE: True,
        DocumentClassification.SUITABILITY_ASSESSMENT: False,
        DocumentClassification.KYC_DOCUMENT: False,
        DocumentClassification.LEGAL_OPINION: "own",
        DocumentClassification.PRIVILEGED: False,
    },
    AdvisorRole.SENIOR_COUNSEL: {
        DocumentClassification.REGULATORY_GUIDANCE: True,
        DocumentClassification.SUITABILITY_ASSESSMENT: True,
        DocumentClassification.KYC_DOCUMENT: True,
        DocumentClassification.LEGAL_OPINION: True,
        DocumentClassification.PRIVILEGED: "own",
    },
}


@dataclass
class AdvisorStore:
    """In-process advisor registry. Replaced by identity provider in production."""

    advisors: dict[str, AdvisorProfile] = field(default_factory=dict)

    def register(self, profile: AdvisorProfile) -> None:
        self.advisors[profile.advisor_id] = profile

    def get(self, advisor_id: str) -> AdvisorProfile | None:
        return self.advisors.get(advisor_id)


_advisor_store = AdvisorStore()


def get_advisor_store() -> AdvisorStore:
    return _advisor_store


def authorize_retrieval(
    advisor_id: str,
    classification: DocumentClassification,
    client_id: str | None = None,
    agent_identity: str = "",
) -> AuthorizationResult:
    """Check whether this advisor can access documents of this classification
    for this client.

    Fail-closed: if the advisor is unknown, the classification is unknown,
    or the access matrix denies, nothing is returned. Not a filtered subset.
    Nothing.

    Authorization input tuple (for OPA migration):
      user_identity  = advisor_id
      agent_identity = which agent is requesting
      resource       = classification
      action         = "retrieve"
      arguments      = {"client_id": client_id}
      context        = advisor profile (role, client_ids)
    """
    advisor = _advisor_store.get(advisor_id)
    if advisor is None:
        logger.warning("Unknown advisor: %s (agent=%s)", advisor_id, agent_identity)
        return AuthorizationResult(
            allowed=False,
            reason=f"Advisor '{advisor_id}' not registered",
            advisor_id=advisor_id,
            agent_identity=agent_identity,
            requested_classification=classification.value,
            requested_client_id=client_id or "",
        )

    role_access = _ACCESS_MATRIX.get(advisor.role, {})
    access_level = role_access.get(classification, False)

    # Denied outright
    if access_level is False:
        logger.info(
            "Denied: %s (%s) cannot access %s (agent=%s)",
            advisor_id, advisor.role.value, classification.value, agent_identity,
        )
        return AuthorizationResult(
            allowed=False,
            reason=f"Role '{advisor.role.value}' denied access to '{classification.value}'",
            advisor_id=advisor_id,
            agent_identity=agent_identity,
            requested_classification=classification.value,
            requested_client_id=client_id or "",
        )

    # "own" requires client_id and client must be in advisor's list
    if access_level == "own":
        if not client_id:
            return AuthorizationResult(
                allowed=False,
                reason=f"Classification '{classification.value}' requires a client_id for role '{advisor.role.value}'",
                advisor_id=advisor_id,
                agent_identity=agent_identity,
                requested_classification=classification.value,
                requested_client_id="",
            )
        if client_id not in advisor.client_ids:
            return AuthorizationResult(
                allowed=False,
                reason=f"Advisor '{advisor_id}' not authorized for client '{client_id}'",
                advisor_id=advisor_id,
                agent_identity=agent_identity,
                requested_classification=classification.value,
                requested_client_id=client_id,
            )

    # Allowed (True or "own" with valid client)
    return AuthorizationResult(
        allowed=True,
        reason="Authorized",
        advisor_id=advisor_id,
        agent_identity=agent_identity,
        requested_classification=classification.value,
        requested_client_id=client_id or "",
    )
