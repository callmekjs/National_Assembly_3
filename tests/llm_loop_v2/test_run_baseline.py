from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "run_baseline.py"
SPEC = importlib.util.spec_from_file_location("run_baseline_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_safe_settings_excludes_secrets_and_honors_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANSWER_MODEL=file-model\nOPENAI_API_KEY=secret\nRERANKER_ENABLED=1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANSWER_MODEL", "runtime-model")
    monkeypatch.setenv("RERANKER_ENABLED", "0")
    settings = MODULE.safe_settings(env_file)
    assert settings["ANSWER_MODEL"] == "runtime-model"
    assert settings["RERANKER_ENABLED"] == "0"
    assert "OPENAI_API_KEY" not in settings


def test_runtime_hashes_requires_every_file(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, "RUNTIME_FILES", ("backend/answer.py",))
    path = tmp_path / "backend" / "answer.py"
    path.parent.mkdir()
    path.write_text("value = 1\n", encoding="utf-8")
    hashes = MODULE.runtime_hashes(tmp_path)
    assert hashes["backend/answer.py"] == MODULE.sha256(path)


def test_runtime_hashes_include_evaluator_and_frontend_request_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, "RUNTIME_FILES", ())
    backend = tmp_path / "backend" / "answer.py"
    evaluator = tmp_path / "scripts" / "llm_loop_v2" / "score_deterministic.py"
    frontend = tmp_path / "frontend" / "src" / "api.js"
    for path in (backend, evaluator, frontend):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("version = 1\n", encoding="utf-8")
    hashes = MODULE.runtime_hashes(tmp_path)
    assert {"backend/answer.py", "scripts/llm_loop_v2/score_deterministic.py", "frontend/src/api.js"}.issubset(hashes)


def test_resume_rejects_changed_runtime_hash(tmp_path):
    path = tmp_path / "config.json"
    base = {
        "git_head": "head", "dataset": "blind.jsonl", "dataset_sha256": "data",
        "base_url": "http://local", "requested_records": 100, "safe_settings": {},
        "runtime_sha256": {"answer.py": "old"}, "started_at": "first",
    }
    MODULE.write_or_verify_config(path, base)
    changed = {**base, "runtime_sha256": {"answer.py": "new"}, "started_at": "second"}
    try:
        MODULE.write_or_verify_config(path, changed)
    except ValueError as exc:
        assert "mixed resume" in str(exc)
    else:
        raise AssertionError("mixed runtime resume must fail")


def test_resume_allows_only_started_at_to_change(tmp_path):
    path = tmp_path / "config.json"
    base = {
        "git_head": "head", "dataset": "blind.jsonl", "dataset_sha256": "data",
        "base_url": "http://local", "requested_records": 100, "safe_settings": {},
        "runtime_sha256": {"answer.py": "same"}, "started_at": "first",
    }
    MODULE.write_or_verify_config(path, base)
    resumed = MODULE.write_or_verify_config(path, {**base, "started_at": "second"})
    assert resumed["started_at"] == "first"


def test_evaluation_query_body_matches_frontend_semantic_inputs():
    record = {
        "question": "2024년 1월 1일 정무위원회에서 무엇을 논의했는가?",
        "mode": "qa",
        "filters": {"committee": "정무위", "date_from": "2024-01-01", "date_to": "2024-01-01"},
    }
    body = MODULE.query_body(record)
    assert body == {
        "question": record["question"],
        "mode": "qa",
        "include_trace": True,
    }
    assert not {"committee", "date_from", "date_to"} & body.keys()


def test_accumulated_cost_uses_answer_and_reranker_combined_usage():
    rows = [
        {"response": {"usage": {"est_cost_usd": 0.0123}}},
        {"response": {"usage": {"est_cost_usd": 0.0045}}},
        {"response": {"usage": None}},
        {"response": {}},
    ]
    assert MODULE.accumulated_cost(rows) == 0.0168


def test_successful_result_ids_rejects_failed_attempts():
    rows = [{"id": "dev-001", "http_status": 0, "error": {"type": "TimeoutError"}}]
    try:
        MODULE.successful_result_ids(rows)
    except ValueError as exc:
        assert "failed attempts" in str(exc)
    else:
        raise AssertionError("a failed attempt must never become a completed resume id")


def test_successful_result_ids_rejects_duplicates():
    rows = [
        {"id": "dev-001", "http_status": 200, "error": None},
        {"id": "dev-001", "http_status": 200, "error": None},
    ]
    try:
        MODULE.successful_result_ids(rows)
    except ValueError as exc:
        assert "duplicate ids" in str(exc)
    else:
        raise AssertionError("duplicate result IDs must be rejected")
