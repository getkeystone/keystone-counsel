"""Regression test: cross-client retrieval isolation.

Proves the client_id enforcement added to the vector store query paths.
An advisor scoped to one client must never retrieve another client's chunks,
even at the same classification. Global chunks (client_id None) stay
retrievable by any caller.

This is the retrieval-layer counterpart to the authorization-matrix tests in
test_smoke.py: those test the authorize_retrieval() decision; this tests that
the store actually excludes other clients' rows from the returned results.

The in-memory store is exercised directly (no live DB needed). PgVectorStore
applies the identical predicate in SQL:
    (client_id IS NULL OR client_id = :caller_client_id)
"""

from keystone_counsel.vectorstore import Chunk, InMemoryVectorStore

_EMB = [0.1, 0.2, 0.3, 0.4]
_CLASS = "kyc_document"


def _store_with_three_clients() -> InMemoryVectorStore:
    """One chunk each for client-a, client-b, and global (None), all at the
    same classification so only the client filter can separate them."""
    store = InMemoryVectorStore()
    store.add(
        Chunk(chunk_id="a1", content="client A KYC record", source_document="a.md",
              section="s", classification=_CLASS, client_id="client-a"), _EMB)
    store.add(
        Chunk(chunk_id="b1", content="client B KYC record", source_document="b.md",
              section="s", classification=_CLASS, client_id="client-b"), _EMB)
    store.add(
        Chunk(chunk_id="g1", content="shared regulatory note", source_document="g.md",
              section="s", classification=_CLASS, client_id=None), _EMB)
    return store


def _chunk_ids(results) -> set:
    return {r.chunk.chunk_id for r in results}


def _client_ids(results) -> set:
    return {r.chunk.client_id for r in results}


def test_cross_client_retrieval_denied():
    store = _store_with_three_clients()

    # Query as client-A: own chunk + global, never client-B.
    res_a = store.query(_EMB, k=10, allowed_classifications=[_CLASS],
                        caller_client_id="client-a")
    ids_a = _chunk_ids(res_a)
    assert "a1" in ids_a, "client-A must see its own chunk"
    assert "g1" in ids_a, "global (client_id None) chunk must be visible"
    assert "b1" not in ids_a, "client-A must NOT see client-B's chunk"
    assert "client-b" not in _client_ids(res_a)

    # Query as client-B: own chunk + global, never client-A.
    res_b = store.query(_EMB, k=10, allowed_classifications=[_CLASS],
                        caller_client_id="client-b")
    ids_b = _chunk_ids(res_b)
    assert "b1" in ids_b, "client-B must see its own chunk"
    assert "g1" in ids_b, "global chunk must be visible"
    assert "a1" not in ids_b, "client-B must NOT see client-A's chunk"
    assert "client-a" not in _client_ids(res_b)

    # No client context: only global content, zero client-specific rows.
    res_none = store.query(_EMB, k=10, allowed_classifications=[_CLASS],
                           caller_client_id=None)
    ids_none = _chunk_ids(res_none)
    assert ids_none == {"g1"}, "no client context must return only global content"
    assert "a1" not in ids_none and "b1" not in ids_none


def test_cross_client_denied_without_classification_filter():
    """The client boundary must hold even when no classification filter is
    supplied (the previously unfiltered branch that returned everything)."""
    store = _store_with_three_clients()

    res_a = store.query(_EMB, k=10, allowed_classifications=None,
                        caller_client_id="client-a")
    ids_a = _chunk_ids(res_a)
    assert "a1" in ids_a and "g1" in ids_a
    assert "b1" not in ids_a, "client-A must NOT see client-B even with no classification filter"
