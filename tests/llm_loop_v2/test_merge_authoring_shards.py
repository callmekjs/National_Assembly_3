from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[2]


def load(name, filename):
    path = ROOT / "scripts" / "llm_loop_v2" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


AUTHOR = load("author_shard_test", "author_gold.py")
MERGE = load("merge_authoring_test", "merge_authoring_shards.py")
REVIEW_MERGE = load("merge_review_test", "merge_review_decisions.py")


def test_authoring_id_selection_preserves_queue_order():
    queue = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    assert AUTHOR.select_records(queue, ["c", "a"]) == [queue[0], queue[2]]


def test_merge_requires_exact_unique_queue_ids():
    queue = [{"id": "a", "candidate_id": "ca"}, {"id": "b", "candidate_id": "cb"}]
    merged = MERGE.merge_rows(
        queue,
        [[{"id": "b", "candidate_id": "cb"}], [{"id": "a", "candidate_id": "ca"}]],
    )
    assert [row["id"] for row in merged] == ["a", "b"]
    try:
        MERGE.merge_rows(queue, [[{"id": "a", "candidate_id": "ca"}]])
    except ValueError as exc:
        assert "missing result ids" in str(exc)
    else:
        raise AssertionError("an incomplete merge must fail")


def test_overlay_replaces_only_selected_ids():
    queue = [{"id": "a", "candidate_id": "ca"}, {"id": "b", "candidate_id": "cb"}]
    base = [
        {"id": "a", "candidate_id": "ca", "version": 1},
        {"id": "b", "candidate_id": "cb", "version": 1},
    ]
    result = MERGE.overlay_rows(
        queue, base, [[{"id": "b", "candidate_id": "cb", "version": 2}]],
    )
    assert result == [base[0], {"id": "b", "candidate_id": "cb", "version": 2}]


def test_validation_report_selects_union_of_failures_and_flags(tmp_path):
    report = tmp_path / "validation.json"
    report.write_text(
        '{"hard_errors":[{"id":"a"}],"review_flags":[{"id":"b"},{"id":"a"}]}',
        encoding="utf-8",
    )
    assert AUTHOR.ids_from_validation(report) == ["a", "b"]


def test_reference_values_are_limited_to_scope_metadata():
    assert AUTHOR.SCHEMA["schema"]["properties"]["reference_values"]["maxItems"] == 3


def test_review_selects_rejected_or_incomplete_items(tmp_path):
    review = tmp_path / "review.jsonl"
    review.write_text(
        '{"id":"a","status":"approved","checks":{"scope":true}}\n'
        '{"id":"b","status":"rejected","checks":{"scope":false}}\n'
        '{"id":"c","status":"approved","checks":{"scope":false}}\n',
        encoding="utf-8",
    )
    assert AUTHOR.ids_from_review(review) == ["b", "c"]


def test_review_recheck_replaces_only_matching_id():
    base = [{"id": "a", "status": "approved"}, {"id": "b", "status": "rejected"}]
    replacement = [{"id": "b", "status": "approved"}]
    assert REVIEW_MERGE.merge(base, replacement) == [
        base[0], {"id": "b", "status": "approved"},
    ]
