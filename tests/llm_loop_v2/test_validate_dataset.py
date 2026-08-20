from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "validate_dataset.py"
SPEC = importlib.util.spec_from_file_location("validate_dataset_v2", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def valid_record() -> dict:
    return {
        "schema_version": "2.0",
        "id": "dev-001",
        "split": "dev",
        "category": "fact",
        "question": "홍길동 의원은 어떤 내용을 질의했는가?",
        "mode": "qa",
        "answerable": True,
        "filters": {"committee": "예시위", "date_from": "2024-01-01", "date_to": "2024-01-01"},
        "gold": {
            "answer": "홍길동 의원은 예산 집행을 질의했다.",
            "required_claims": ["홍길동 의원이 예산 집행을 질의했다."],
            "forbidden_claims": ["예산 삭감이 확정됐다."],
            "exact_values": [{"type": "person", "value": "홍길동"}],
            "evidence": [{
                "chunk_id": "chunk-example-1",
                "source_id": "source-example-1",
                "quote": "홍길동 의원은 예산 집행 내역을 제출해 달라고 질의하였다.",
                "speaker": "홍길동",
                "role": "의원",
                "committee": "예시위",
                "date": "2024-01-01",
                "page_start": 10,
            }],
            "refusal_reason": None,
        },
        "provenance": {
            "selection_method": "source_first_v2",
            "created_at": "2026-08-17T22:00:00+09:00",
            "review_status": "source_checked",
        },
    }


class DatasetValidationTests(unittest.TestCase):
    def test_valid_answerable_record(self):
        MODULE.validate_record(valid_record(), 1, "dev")

    def test_required_and_reference_values_form_exact_value_union(self):
        record = valid_record()
        required = {"type": "number", "value": "10명"}
        reference = {"type": "number", "value": "93조 원"}
        record["gold"]["required_exact_values"] = [required]
        record["gold"]["reference_values"] = [reference]
        record["gold"]["exact_values"] = [required, reference]
        MODULE.validate_record(record, 1, "dev")

    def test_partitioned_values_must_match_exact_values(self):
        record = valid_record()
        record["gold"]["required_exact_values"] = []
        record["gold"]["reference_values"] = []
        with self.assertRaises(MODULE.DatasetError):
            MODULE.validate_record(record, 1, "dev")

    def test_value_cannot_be_required_and_reference_at_once(self):
        record = valid_record()
        value = {"type": "person", "value": "홍길동"}
        record["gold"]["required_exact_values"] = [value]
        record["gold"]["reference_values"] = [value]
        record["gold"]["exact_values"] = [value]
        with self.assertRaises(MODULE.DatasetError):
            MODULE.validate_record(record, 1, "dev")

    def test_answerable_requires_evidence(self):
        record = valid_record()
        record["gold"]["evidence"] = []
        with self.assertRaises(MODULE.DatasetError):
            MODULE.validate_record(record, 1, "dev")

    def test_unanswerable_cannot_hide_gold_answer(self):
        record = valid_record()
        record["category"] = "unanswerable"
        record["answerable"] = False
        record["gold"]["required_claims"] = []
        record["gold"]["evidence"] = []
        record["gold"]["refusal_reason"] = "원문에 해당 정보가 없다."
        with self.assertRaises(MODULE.DatasetError):
            MODULE.validate_record(record, 1, "dev")

    def test_duplicate_questions_fail(self):
        first = valid_record()
        second = valid_record()
        second["id"] = "dev-002"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dev.jsonl"
            path.write_text(
                json.dumps(first, ensure_ascii=False) + "\n" +
                json.dumps(second, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.DatasetError):
                MODULE.load_and_validate(path, "dev")


if __name__ == "__main__":
    unittest.main()
