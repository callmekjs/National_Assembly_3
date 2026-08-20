from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "select_authoring_queue.py"
SPEC = importlib.util.spec_from_file_location("select_authoring_queue", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def source(index: int, text: str, committee: str = "정무위원회"):
    return {
        "candidate_id": f"candidate-{index:03d}",
        "text": text,
        "committee": committee,
        "committee_filter": "정무위",
    }


def test_risk_filter_catches_context_and_truncation():
    assert MODULE.RISK.search("아까 그 부분은 발언시간 초과로 마이크 중단")
    assert not MODULE.RISK.search("예산은 10억 원에서 12억 원으로 증가했다.")


def test_scores_prefer_numeric_comparison_for_timeline():
    numeric = source(1, "2024년 10억 원에서 2025년 20억 원으로 증가했다. 반면 지원자는 감소했다.")
    plain = source(2, "정책을 검토할 필요가 있다고 말했다.")
    assert MODULE.scores(numeric)["comparison_timeline"] > MODULE.scores(plain)["comparison_timeline"]


def test_balanced_pick_keeps_sparse_committee_sources_for_dev():
    pool = [
        source(index, "예산은 10억 원에서 12억 원으로 증가했다.", committee)
        for index, committee in enumerate(
            ["재정경제기획위원회"] * 3 + ["정무위원회"] * 10,
            start=1,
        )
    ]
    selected = MODULE.balanced_pick(pool, "comparison_timeline", 5, reserve_per_committee=2)
    sparse_selected = sum(row["committee"] == "재정경제기획위원회" for row in selected)
    assert sparse_selected <= 1
