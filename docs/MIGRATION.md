# Authorization Migration: In-Process to OPA

## Current state (Phase 1)

Authorization is an in-process Python module (`keystone_counsel/auth.py`) with
a hardcoded access matrix. The matrix encodes role-classification-client rules.
Authorization decisions are logged to the audit chain.

## Target state (Phase 2, graduation path Stage 1.1)

Authorization decisions are made by Open Policy Agent (OPA) or AWS Cedar.
The policy is code, versioned in a Git repository (`keystone-policy`), testable
with unit tests against the policy itself.

## OPA input tuple

Every authorization request sends this input to the policy engine:

```json
{
  "user_identity": "advisor-001",
  "agent_identity": "counsel-agent-v1",
  "resource": "suitability_assessment",
  "action": "retrieve",
  "arguments": {
    "client_id": "client-A",
    "jurisdiction": "alberta"
  },
  "context": {
    "advisor_role": "suitability_advisor",
    "advisor_client_ids": ["client-A", "client-B"],
    "session_id": "...",
    "request_timestamp": "..."
  }
}
```

## What stays the same

- The `authorize_retrieval()` function signature
- The `AuthorizationResult` return type
- Audit logging of every decision (allow and deny)
- Fail-closed behavior on any authorization failure

## What changes

- The access matrix moves from Python dict to OPA Rego policy
- The advisor store moves from in-process dict to identity provider query
- The `decision_source` field on `AuthorizationResult` changes from
  `"in-process"` to `"opa"`
- Policy changes no longer require code changes or service restarts

## Migration steps

1. Deploy OPA as a sidecar on AnchorNode (or ForgePrime)
2. Write the Rego policy encoding the access matrix
3. Write policy unit tests
4. Update `authorize_retrieval()` to call OPA HTTP API
5. Verify audit log shows `decision_source: opa`
6. Run the eval suite to confirm no authorization regressions

## Why Counsel drives this migration

Engage's authorization model is flat: caller role maps to corpus. The
in-process module handles it cleanly. Counsel's authorization model is
relational: role + client + classification. The access matrix is already
at the edge of what a Python dict handles readably. OPA's Rego language
is designed for exactly this class of policy expression.

Both extensions share the same `authorize_retrieval()` interface. When
OPA ships for Counsel, migrating Engage is a configuration change, not
a code change.
