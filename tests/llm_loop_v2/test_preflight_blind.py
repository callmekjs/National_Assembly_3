from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "preflight_blind.py"
SPEC = importlib.util.spec_from_file_location("preflight_blind_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def make_queue():
    rows = []
    for split, categories in MODULE.EXPECTED.items():
        split_index = 1
        for category, count in categories.items():
            for _ in range(count):
                rows.append({
                    "id": f"{split}-{split_index:03d}", "split": split, "category": category,
                    "candidate_id": f"{split}-candidate-{split_index}",
                    "source_id": f"{split}-source-{split_index}",
                })
                split_index += 1
    return rows


def test_complete_preflight_passes():
    dev = [{"id": f"dev-{index:03d}"} for index in range(1, 21)]
    negatives = [{"id": f"blind-{index:03d}"} for index in range(91, 101)]
    proofs = [{
        "speaker_chunk_count_in_target": 0, "target_chunk_count": 10,
        "target_source_ids": [f"negative-{index}"], "speaker_reference_count_elsewhere": 20,
    } for index in range(10)]
    report = MODULE.preflight(
        dev, make_queue(), negatives, proofs,
        {"remaining_to_cap_usd": 11.2, "within_cap": True},
    )
    assert report["status"] == "PASS"


def test_budget_shortfall_fails():
    dev = [{"id": f"dev-{index:03d}"} for index in range(1, 21)]
    negatives = [{"id": f"blind-{index:03d}"} for index in range(91, 101)]
    proofs = [{
        "speaker_chunk_count_in_target": 0, "target_chunk_count": 10,
        "target_source_ids": [f"negative-{index}"], "speaker_reference_count_elsewhere": 20,
    } for index in range(10)]
    report = MODULE.preflight(
        dev, make_queue(), negatives, proofs,
        {"remaining_to_cap_usd": 10.9, "within_cap": True},
    )
    assert report["status"] == "FAIL"
    assert report["checks"]["budget_reserve_gte_11_00"] is False


def test_final_stage_reserves_only_remaining_blind_cost():
    dev = [{"id": f"dev-{index:03d}"} for index in range(1, 21)]
    negatives = [{"id": f"blind-{index:03d}"} for index in range(91, 101)]
    proofs = [{
        "speaker_chunk_count_in_target": 0, "target_chunk_count": 10,
        "target_source_ids": [f"negative-{index}"], "speaker_reference_count_elsewhere": 20,
    } for index in range(10)]
    report = MODULE.preflight(
        dev, make_queue(), negatives, proofs,
        {"remaining_to_cap_usd": 2.1, "within_cap": True},
        stage="final",
    )
    assert report["status"] == "PASS"
    assert report["checks"]["final_blind_budget_available"] is True


def test_incremental_budget_is_preferred_over_total_cap_remainder():
    dev = [{"id": f"dev-{index:03d}"} for index in range(1, 21)]
    negatives = [{"id": f"blind-{index:03d}"} for index in range(91, 101)]
    proofs = [{
        "speaker_chunk_count_in_target": 0, "target_chunk_count": 10,
        "target_source_ids": [f"negative-{index}"], "speaker_reference_count_elsewhere": 20,
    } for index in range(10)]
    report = MODULE.preflight(
        dev, make_queue(), negatives, proofs,
        {
            "remaining_to_cap_usd": 6.2, "within_cap": True,
            "additional_remaining_usd": 5.7, "within_additional_cap": True,
        },
        required_budget=5.8,
    )
    assert report["status"] == "FAIL"
    assert report["remaining_budget_usd"] == 5.7
