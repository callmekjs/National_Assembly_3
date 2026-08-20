from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "select_dev_queue.py"
SPEC = importlib.util.spec_from_file_location("select_dev_queue_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_dev_queue_is_disjoint_balanced_and_fixed_size():
    committees = list(MODULE.COMMITTEE_FILTERS)
    records = []
    for index in range(45):
        committee = committees[index % len(committees)]
        records.append({
            "candidate_id": f"candidate-{index:03d}",
            "chunk_id": f"chunk-{index}",
            "source_id": f"source-{index}",
            "committee": committee,
            "speaker": "홍길동",
            "text": "2025년 사업 예산은 10억 원에서 12억 원으로 증가했다. " * 6,
        })
    excluded = [{"source_id": "source-0"}, {"source_id": "source-1"}]
    queue = MODULE.build_dev_queue(records, excluded)
    assert len(queue) == 18
    assert len({row["source_id"] for row in queue}) == 18
    assert not ({"source-0", "source-1"} & {row["source_id"] for row in queue})
    assert max(__import__("collections").Counter(row["committee"] for row in queue).values()) <= 3
    assert __import__("collections").Counter(row["category"] for row in queue) == MODULE.TARGETS
