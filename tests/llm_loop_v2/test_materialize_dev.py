from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "materialize_dev.py"
SPEC = importlib.util.spec_from_file_location("materialize_dev_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def record(row_id: str) -> dict:
    return {"id": row_id, "provenance": {"review_status": "source_checked"}}


def test_dev_materialization_requires_exact_ids_and_approvals():
    queue = [{"id": f"dev-{index:03d}"} for index in range(1, 19)]
    results = [
        {"id": row["id"], "record": record(row["id"])}
        for row in queue
    ]
    negatives = [record("dev-019"), record("dev-020")]
    approvals = {f"dev-{index:03d}" for index in range(1, 21)}
    dev = MODULE.materialize_dev(queue, results, negatives, approvals)
    assert [row["id"] for row in dev] == [f"dev-{index:03d}" for index in range(1, 21)]
    assert all(row["provenance"]["review_status"] == "frozen" for row in dev)


def test_dev_materialization_rejects_incomplete_approval():
    queue = [{"id": f"dev-{index:03d}"} for index in range(1, 19)]
    results = [{"id": row["id"], "record": record(row["id"])} for row in queue]
    negatives = [record("dev-019"), record("dev-020")]
    try:
        MODULE.materialize_dev(queue, results, negatives, {f"dev-{index:03d}" for index in range(1, 20)})
    except ValueError as exc:
        assert "all 20" in str(exc)
    else:
        raise AssertionError("incomplete dev approval must fail")
