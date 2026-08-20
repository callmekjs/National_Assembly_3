from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "promote_failures.py"
SPEC = importlib.util.spec_from_file_location("promote_failures_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_promote_preserves_gold_and_marks_development(tmp_path):
    source = tmp_path / "blind.jsonl"
    output = tmp_path / "dev.jsonl"
    rows = [
        {"id": record_id, "split": "blind", "gold": {"answer": record_id}, "provenance": {}}
        for record_id in MODULE.DEFAULT_IDS
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    promoted = MODULE.promote(source, output)
    assert len(promoted) == 4
    assert all(row["split"] == "dev" for row in promoted)
    assert [row["id"] for row in promoted] == [f"dev-{100 + index:03d}" for index in range(1, 5)]
    assert [row["provenance"]["promoted_from"] for row in promoted] == list(MODULE.DEFAULT_IDS)
    assert [row["gold"]["answer"] for row in promoted] == list(MODULE.DEFAULT_IDS)
