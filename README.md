# Keystone Counsel

Regulated content RAG for legal and financial advisory. Part of the [Keystone Applied Intelligence](https://getkeystone.ai) platform.

## What this is

Counsel is a retrieval system for regulated content where authorization determines what can be retrieved, not just who can ask. The authorization model evaluates three dimensions before any retrieval executes:

- **Advisor role**: suitability advisor, compliance officer, associate counsel, senior counsel
- **Client relationship**: which clients this advisor is authorized to act for
- **Document classification**: regulatory guidance, suitability assessments, KYC documents, legal opinions, privileged material

If the authorization check fails, nothing is returned. Not a filtered subset. Nothing. Fail-closed.

## Authorization matrix

| Role | Regulatory | Suitability | KYC | Legal Opinion | Privileged |
|------|-----------|-------------|-----|---------------|------------|
| Suitability Advisor | any | own clients | own clients | denied | denied |
| Compliance Officer | any | any | any | denied | denied |
| Associate Counsel | any | denied | denied | own clients | denied |
| Senior Counsel | any | any | any | any | own clients |

## Contact center heritage

The authorization model is the skill-based routing matrix from workforce management: an agent (advisor) has skills (role), assigned queues (clients), and the routing engine checks all three dimensions before delivering the interaction (document).

## Stack

- Python 3.11+, FastAPI, uv
- PostgreSQL 16 + pgvector (AnchorNode)
- qwen2.5:7b-instruct + nomic-embed-text via Ollama (ZenithForge)
- OpenTelemetry GenAI semantic conventions
- Hash-chained audit ledger (platform-consistent with keystone-engage)

## Development

```bash
uv sync
make test    # run tests
make run     # start dev server on :8200
```

## Eval ledger

Follows the `keystone-{component}/{type}-v{n}` convention. Planned entry: `keystone-counsel/retrieval-v1`.

## Platform

Counsel is the second extension in the Keystone platform. It shares the substrate (agents table, tasks table, audit chain format, OTel conventions) with [keystone-engage](https://github.com/getkeystone/keystone-engage). The same authorization interface accepts `agent_identity` so OPA (graduation path Stage 1.1) can enforce different scopes for different agents through a single policy engine.
