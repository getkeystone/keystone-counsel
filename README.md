# Keystone Counsel

Authorization-first retrieval for regulated content.

## What this is

Regulated advisory work in legal, financial, and compliance domains has a property most retrieval systems ignore: who may see a document depends on the actor's role, their relationship to the client the document belongs to, and the document's classification. Returning the wrong document is not a user-experience issue. It is a compliance failure.

Keystone Counsel decides authorization before retrieval runs. It filters candidates by authorized classification and by client relationship at the retrieval layer, refuses when authorization or evidence is insufficient, and gates generated answers on a confidence threshold. Denied content does not reach the model.

Counsel runs on customer-controlled infrastructure with local models through Ollama. There is no external API dependency for core operation.

## Architecture

A request to `POST /counsel` (`api.py`) runs a fixed pipeline:

1. Open an audit entry.
2. Resolve the requested document classifications (an explicit filter, or the default advisory set).
3. Authorize each classification for the advisor and client (`authorize_retrieval`, `auth.py`). If none are authorized, the request fails closed and returns nothing.
4. Retrieve from the authorized set, filtered by classification and by client at the retrieval layer (`rag.py` calling the vector store).
5. Gate the best result on the confidence threshold. Below it, the response fails closed with no answer.
6. Generate a cited answer from the retrieved evidence with a local model.
7. Close the audit entry.

The audit trail is hash-chained with SHA-256: each entry records the prior entry's hash, and `verify_chain` (`audit.py`) walks the full chain to detect a break. Hashing is unkeyed SHA-256, not a keyed HMAC. The PostgreSQL backend (`pgaudit.py`) uses the same format and verifier.

## Authorization model

Authorization has three dimensions, enforced at two points in the pipeline: a role decision before retrieval, and two retrieval-layer filters.

**Role (the decision).** `authorize_retrieval` checks an access matrix of role by classification before retrieval runs. Each cell is "any client," "own clients only," or "denied." A suitability advisor can read regulatory guidance for any client but KYC only for its own clients; an associate counsel can read legal opinions only for its own clients; a senior counsel can read privileged material only for its own clients. An unknown advisor, an unknown classification, or a denied cell returns nothing, not a filtered subset.

**Classification (retrieval filter).** On the pgvector path, the authorized classifications are a `WHERE classification = ANY(...)` predicate at the database layer, so denied classifications never enter the candidate set regardless of similarity score. On the in-memory path, the same filter runs in application code. The database path is the primary gate; the in-memory path is for local development and tests.

**Client isolation (retrieval filter).** Alongside the classification predicate, retrieval filters by client at the same layer:

```sql
(client_id IS NULL OR client_id = :caller_client_id)
```

Global content (client_id NULL, such as public regulatory guidance) is retrievable by any authorized caller. Client-specific content is retrievable only for the matching client. A request with no client context returns only global content and zero client-specific rows, which is fail-closed for confidential data. The in-memory path applies the equivalent filter.

Client isolation was added recently. Earlier, retrieval filtered on classification only, so an advisor scoped to one client could retrieve another client's same-classification chunks. A regression test, `test_cross_client_retrieval_denied` (`tests/test_cross_client.py`), proves cross-client denial on both the classification-filtered and the unfiltered retrieval branches.

The shipped corpus is entirely global content (client_id NULL). The client isolation path is enforced and covered by regression tests, but no client-specific documents are currently in the corpus.

## Evaluation

The published eval is a retrieval evaluation over a 32-chunk corpus spanning four document classifications. It measures classification-aware filtering and confidence-threshold behavior. It does not test client isolation.

There is no eval artifact in this repository; `evals/` is empty. The published ledger entry lives in [keystone-ledger](https://github.com/getkeystone/keystone-ledger). This repo does not carry an eval artifact of its own, and the README does not claim one.

The cross-client regression test is the current evidence for client isolation. The test suite has 18 tests: 16 covering the authorization matrix, the API, and the audit chain, plus the 2 new cross-client tests. Run them with `python -m pytest`.

The confidence threshold defaults to 0.50 in code (`config.py`) and is configurable by environment variable.

## Related repos

- [keystone-ledger](https://github.com/getkeystone/keystone-ledger): evaluation lineage and proof artifacts. Public shortly.
- [keystone-verify](https://github.com/getkeystone/keystone-verify): the evaluation framework as a standalone tool. Public.
- [keystone-engage](https://github.com/getkeystone/keystone-engage): governed conversational agent for regulated customer interaction. Public shortly.
- [keystone-gov](https://github.com/getkeystone/keystone-gov): governed RAG for regulated enterprise content. Public shortly.

## License

Apache-2.0. See [LICENSE](LICENSE).
