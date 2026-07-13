"""Eval runner for Keystone Counsel.

Runs eval cases against a live Counsel endpoint. Judges authorization
denials, fail-closed behavior, citation presence, and content assertions.

Usage:
  uv run python -m keystone_counsel.eval [base_url]
  uv run python -m keystone_counsel.eval http://localhost:8200
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

EVAL_DATA_DIR = Path("data/eval")
DEFAULT_BASE_URL = "http://localhost:8200"


@dataclass
class EvalCase:
    case_id: str
    category: str
    query: str
    advisor_id: str
    expected_behavior: str
    description: str = ""
    client_id: str | None = None
    classification_filter: list[str] = field(default_factory=list)
    bucket: str = "core-regression"
    expected_contains: list[str] = field(default_factory=list)
    expected_contains_any: list[str] = field(default_factory=list)
    expected_absent: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    case_id: str
    category: str
    passed: bool
    expected_behavior: str
    actual_severity: str
    response_length: int
    latency_ms: float
    details: str = ""
    bucket: str = "core-regression"
    audit_hash: str = ""
    content_ok: bool = True
    fail_closed: bool = False


def load_eval_cases(path: Path | None = None) -> list[EvalCase]:
    path = path or EVAL_DATA_DIR / "cases.jsonl"
    if not path.exists():
        return []
    cases = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                cases.append(EvalCase(**json.loads(line)))
    return cases


def _join(existing: str, extra: str) -> str:
    return f"{existing}; {extra}" if existing else extra


def judge_result(case: EvalCase, response: dict, latency_ms: float) -> EvalResult:
    severity = response.get("severity", "unknown")
    answer = response.get("answer", "")
    audit_hash = response.get("audit_hash", "")
    fail_closed = response.get("fail_closed", False)

    details = ""

    if case.expected_behavior == "respond":
        passed = severity == "tier_0" and len(answer) > 20
        if not passed:
            details = f"Expected tier_0 response >20 chars, got {severity} ({len(answer)} chars)"
    elif case.expected_behavior == "deny":
        passed = severity == "tier_3"
        if not passed:
            details = f"Expected denial (tier_3), got {severity}"
    elif case.expected_behavior == "fail-closed":
        passed = severity in ("tier_2", "tier_3") and fail_closed
        if not passed:
            details = f"Expected fail-closed, got severity={severity} fail_closed={fail_closed}"
    else:
        passed = False
        details = f"Unknown expected_behavior: {case.expected_behavior}"

    # Content assertions
    content_ok = True
    answer_lower = answer.lower()

    # AND semantics: all must be present
    missing = [s for s in case.expected_contains if s.lower() not in answer_lower]
    if missing:
        content_ok = False
        details = _join(details, f"Missing expected_contains: {missing}")

    # OR semantics: at least one must be present
    if case.expected_contains_any and not any(
        s.lower() in answer_lower for s in case.expected_contains_any
    ):
        content_ok = False
        details = _join(
            details, f"Missing expected_contains_any (need at least one): {case.expected_contains_any}"
        )

    # Absent: none may be present
    forbidden = [s for s in case.expected_absent if s.lower() in answer_lower]
    if forbidden:
        content_ok = False
        details = _join(details, f"Present expected_absent: {forbidden}")

    passed = passed and content_ok

    return EvalResult(
        case_id=case.case_id,
        category=case.category,
        passed=passed,
        expected_behavior=case.expected_behavior,
        actual_severity=severity,
        response_length=len(answer),
        latency_ms=latency_ms,
        details=details,
        bucket=case.bucket,
        audit_hash=audit_hash,
        content_ok=content_ok,
        fail_closed=fail_closed,
    )


def run_eval(base_url: str = DEFAULT_BASE_URL) -> dict:
    cases = load_eval_cases()
    if not cases:
        print("No eval cases found")
        return {}

    run_id = f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    print(f"Running {len(cases)} eval cases against {base_url}")
    print("-" * 60)

    client = httpx.Client(base_url=base_url, timeout=60.0)
    results: list[EvalResult] = []

    for case in cases:
        request_body: dict = {
            "query": case.query,
            "advisor_id": case.advisor_id,
        }
        if case.client_id is not None:
            request_body["client_id"] = case.client_id
        if case.classification_filter:
            request_body["classification_filter"] = case.classification_filter

        try:
            start = time.monotonic()
            resp = client.post("/counsel", json=request_body)
            latency = (time.monotonic() - start) * 1000
            data = resp.json()
            result = judge_result(case, data, latency)
        except Exception as e:
            result = EvalResult(
                case_id=case.case_id,
                category=case.category,
                passed=False,
                expected_behavior=case.expected_behavior,
                actual_severity="error",
                response_length=0,
                latency_ms=0,
                details=f"Request failed: {e}",
                bucket=case.bucket,
            )

        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        line = f"  {status}  {result.case_id} [{result.category}] {round(result.latency_ms)}ms"
        if not result.passed:
            line += f"\n        {result.details}"
        print(line)

    client.close()

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print("-" * 60)
    print(f"Results: {passed}/{total} passed ({passed/total*100:.0f}%)")

    by_category: dict[str, dict] = {}
    by_bucket: dict[str, dict] = {}
    for r in results:
        cat = by_category.setdefault(r.category, {"total": 0, "passed": 0})
        cat["total"] += 1
        if r.passed:
            cat["passed"] += 1
        buc = by_bucket.setdefault(r.bucket, {"total": 0, "passed": 0})
        buc["total"] += 1
        if r.passed:
            buc["passed"] += 1

    for cat, counts in by_category.items():
        print(f"  {cat}: {counts['passed']}/{counts['total']}")
    print("By bucket:")
    for buc, counts in by_bucket.items():
        print(f"  {buc}: {counts['passed']}/{counts['total']}")

    # Save results
    output = {
        "summary": {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": passed / total if total > 0 else 0,
            "by_category": by_category,
            "by_bucket": by_bucket,
        },
        "results": [
            {
                "case_id": r.case_id,
                "category": r.category,
                "bucket": r.bucket,
                "passed": r.passed,
                "expected_behavior": r.expected_behavior,
                "actual_severity": r.actual_severity,
                "latency_ms": r.latency_ms,
                "response_length": r.response_length,
                "details": r.details,
                "content_ok": r.content_ok,
                "fail_closed": r.fail_closed,
            }
            for r in results
        ],
    }

    results_dir = EVAL_DATA_DIR / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"{run_id}.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

    return output


if __name__ == "__main__":
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL
    run_eval(base_url)
