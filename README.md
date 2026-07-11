# Keystone Counsel

Authorization-first retrieval for regulated legal, financial, and compliance content.

Keystone Counsel is part of the Keystone Applied Intelligence platform. It is a retrieval system for environments where access control is not a UI feature, but a hard requirement of the retrieval path itself.

This is not generic RAG with a permissions note in the prompt. It is a governed retrieval system where authorization determines what can be retrieved before results ever reach the model.[web:106][web:107][web:109]

## What it does

A user query does not go straight to similarity search.

Before retrieval runs, Counsel evaluates three dimensions:

- **Role** — what kind of actor is making the request.
- **Relationship** — which clients or matters that actor is authorized to work on.
- **Classification** — what class of documents is being requested.

If authorization fails, nothing is returned. Not a filtered subset. Nothing.

The system fails closed and enforces access at the retrieval layer, so denied documents never enter the candidate set regardless of similarity score.[web:107][web:109]

## Why it exists

Most retrieval systems treat authorization as a presentation concern: retrieve first, filter later, or tell the model what not to say.

That is not good enough for regulated content.

Legal, financial, and compliance environments require deterministic access control in the retrieval path itself. Keystone Counsel exists to show that governed retrieval should be architectural, not advisory.[web:106][web:107]

## Platform role

Keystone Counsel is one extension in the broader Keystone platform:

- **Engage** proves governed conversational AI for regulated customer interaction.
- **Counsel** proves authorization-first retrieval for regulated content.
- **Verify** proves the evaluation methodology as a reusable tool.

Counsel shares the Keystone substrate for agent identity, task lifecycle, audit chain format, telemetry conventions, and evaluation lineage.

## Core architectural properties

These are structural properties, not prompt instructions:

- authorization evaluated before retrieval,
- fail-closed behavior when authorization cannot be established,
- database-level filtering so denied classifications never appear in results,
- evidence-backed answers with citations,
- tamper-evident audit trails,
- local-first deployment with customer-controlled infrastructure.

## Authorization model

Counsel is designed for domains where access depends on more than login status.

The authorization model combines:

- **actor role** — for example advisor, compliance, or counsel,
- **relationship scope** — which clients, accounts, or matters the actor can act for,
- **document classification** — such as regulatory guidance, KYC material, legal opinions, or privileged content.

This makes retrieval a governed decision, not a ranking-only problem.

## Contact-center heritage

The architecture draws from an older operational discipline: routing systems that do not just ask who is available, but whether the actor has the right role, queue assignment, and scope to receive the work.

Counsel applies the same thinking to AI retrieval. Access is part of routing, not an afterthought.

## Observability

Counsel uses OpenTelemetry GenAI semantic conventions for tracing model calls, retrieval events, token usage, and related AI operations through a shared telemetry vocabulary.[web:93][web:94][web:98][web:110]

## Eval position

Counsel follows the Keystone evaluation discipline:

- claims are backed by eval artifacts,
- fail-closed behavior is tested explicitly,
- authorization behavior is treated as a first-class eval surface,
- failing and passing runs are preserved as part of lineage.

The purpose is not only to answer correctly, but to prove that the system refuses, restricts, and cites correctly under adversarial conditions.

## Current stack

- Python 3.11+
- FastAPI
- PostgreSQL 16 + pgvector
- Ollama for local inference
- OpenTelemetry GenAI semantic conventions
- Docker-based local deployment

## Repo goals

This repository exists to prove that regulated retrieval can be implemented with deterministic authorization at the architectural layer.

Specifically, it aims to show that:

- access control can be enforced before retrieval,
- fail-closed behavior can prevent disclosure under uncertainty,
- document classification can shape retrieval eligibility,
- retrieval governance can be portable across regulated domains,
- evaluation can verify authorization behavior directly.

## Relation to the rest of Keystone

- [`keystone-engage`](https://github.com/getkeystone/keystone-engage) applies the same discipline to governed conversational agents.
- [`keystone-verify`](https://github.com/getkeystone/keystone-verify) extracts the evaluation methodology into a reusable tool.
- [`keystone-kdat`](https://github.com/getkeystone/keystone-kdat) tracks evaluation lineage and proof artifacts.

## License

Apache 2.0
