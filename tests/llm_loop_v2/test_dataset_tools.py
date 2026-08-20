from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


SAMPLER = load("sample_sources_v2", ROOT / "scripts" / "llm_loop_v2" / "sample_sources.py")
FREEZER = load("freeze_dataset_v2", ROOT / "scripts" / "llm_loop_v2" / "freeze_dataset.py")


class SourceSamplerTests(unittest.TestCase):
    def test_procedural_chunk_is_excluded(self):
        record = {
            "chunk_type": "utterance",
            "chunk_id": "c1",
            "source_id": "s1",
            "committee": "예시위원회",
            "meeting_date": "2024-01-01",
            "speaker": "홍길동",
            "text": "의석을 정돈해 주시기 바랍니다. 이제 산회를 선포하겠습니다." * 8,
        }
        self.assertFalse(SAMPLER.eligible(record))

    def test_excluded_dataset_reads_source_ids(self):
        record = {"gold": {"evidence": [{"source_id": "source-1"}, {"source_id": "source-2"}]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dev.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            self.assertEqual(SAMPLER.excluded_source_ids(path), {"source-1", "source-2"})

    def test_sampler_excludes_multiple_datasets_and_negative_proofs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gold = root / "gold.jsonl"
            proof = root / "proof.jsonl"
            gold.write_text(json.dumps({
                "gold": {"evidence": [{"source_id": "gold-source"}]}
            }) + "\n", encoding="utf-8")
            proof.write_text(json.dumps({
                "target_source_ids": ["negative-source-1", "negative-source-2"]
            }) + "\n", encoding="utf-8")
            self.assertEqual(
                SAMPLER.excluded_source_ids([gold, proof]),
                {"gold-source", "negative-source-1", "negative-source-2"},
            )


class DatasetFreezeTests(unittest.TestCase):
    @staticmethod
    def record(source_id: str) -> dict:
        return {"gold": {"evidence": [{"source_id": source_id}]}}

    def test_disjoint_sources_pass(self):
        FREEZER.assert_disjoint({
            "dev": [self.record("source-dev")],
            "blind": [self.record("source-blind")],
            "reserve": [self.record("source-reserve")],
        })

    def test_source_leakage_fails(self):
        with self.assertRaises(ValueError):
            FREEZER.assert_disjoint({
                "dev": [self.record("same-source")],
                "blind": [self.record("same-source")],
            })

    def test_question_filters_must_be_recoverable_without_gold_injection(self):
        analyzer = FREEZER.load_filter_analyzer(ROOT / "scripts" / "llm_loop_v2")
        good = {
            "id": "dev-001",
            "question": "2024년 1월 2일 정무위원회에서 무엇을 논의했습니까?",
            "filters": {"committee": "정무위", "date_from": "2024-01-02", "date_to": "2024-01-02"},
        }
        FREEZER.assert_question_filters({"dev": [good]}, analyzer)

        hidden = dict(good)
        hidden["question"] = "홍길동 위원은 무엇을 논의했습니까?"
        with self.assertRaisesRegex(ValueError, "question/filter mismatch"):
            FREEZER.assert_question_filters({"dev": [hidden]}, analyzer)


if __name__ == "__main__":
    unittest.main()
