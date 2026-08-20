from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "calibrate_verifier_from_judgments.py"
SPEC = importlib.util.spec_from_file_location("calibrate_verifier_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_sources_get_stable_citation_numbers():
    sources = MODULE.sources_from([
        {"chunk_id": "a", "text": "첫 근거"},
        {"chunk_id": "b", "text": "둘째 근거"},
    ])
    assert [source["n"] for source in sources] == [1, 2]
    assert [source["chunk_id"] for source in sources] == ["a", "b"]


def test_old_judge_inputs_recover_original_sparse_citation_numbers():
    sources = MODULE.sources_from(
        [{"chunk_id": "a", "text": "첫 근거"}, {"chunk_id": "b", "text": "둘째 근거"}],
        [1, 4],
    )
    assert [source["n"] for source in sources] == [1, 4]
