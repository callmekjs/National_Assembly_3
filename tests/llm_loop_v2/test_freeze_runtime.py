from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "freeze_runtime.py"
SPEC = importlib.util.spec_from_file_location("freeze_runtime_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_runtime_difference_detects_code_change():
    expected = {"git_head": "h", "safe_settings": {}, "runtime_sha256": {"a.py": "old"}}
    actual = {"git_head": "h", "safe_settings": {}, "runtime_sha256": {"a.py": "new"}}
    assert MODULE.differences(expected, actual) == ["runtime_sha256"]


def test_runtime_difference_accepts_identical_snapshot():
    snapshot = {"git_head": "h", "safe_settings": {"ANSWER_EFFORT": "medium"}, "runtime_sha256": {"a.py": "same"}}
    assert MODULE.differences(snapshot, snapshot) == []
