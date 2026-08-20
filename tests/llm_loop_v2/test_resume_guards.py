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


AUTHOR = load("author_resume_test", "author_gold.py")
JUDGE = load("judge_resume_test", "judge_semantic.py")


def exercise_guard(module, tmp_path, label):
    output = tmp_path / f"{label}.jsonl"
    first = {"model": "model-a", "inputs_sha256": "same"}
    module.ensure_resume_config(output, first)
    assert module.ensure_resume_config(output, first).exists()
    try:
        module.ensure_resume_config(output, {**first, "model": "model-b"})
    except ValueError as exc:
        assert "mixed" in str(exc)
    else:
        raise AssertionError("changed resume config must fail")


def test_authoring_resume_guard(tmp_path):
    exercise_guard(AUTHOR, tmp_path, "author")


def test_judgment_resume_guard(tmp_path):
    exercise_guard(JUDGE, tmp_path, "judge")


def test_orphaned_results_cannot_resume(tmp_path):
    output = tmp_path / "orphan.jsonl"
    output.write_text("{}\n", encoding="utf-8")
    try:
        AUTHOR.ensure_resume_config(output, {"model": "m"})
    except ValueError as exc:
        assert "without resume config" in str(exc)
    else:
        raise AssertionError("orphan results must fail")
