"""원문 청크에서 신규 평가문제 후보를 결정론적으로 추출한다.

기존 data/eval 결과를 읽지 않는다. 동일 seed와 동일 corpus에서는 같은 후보가 나온다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


PROCEDURAL = re.compile(
    r"(개의하겠습니다|산회를 선포|의사일정|상정합니다|정회를 선포|속개하겠습니다|"
    r"감사합니다\.?$|수고하셨습니다\.?$)"
)


def eligible(record: dict[str, Any]) -> bool:
    text = str(record.get("text") or "").strip()
    return (
        record.get("chunk_type") == "utterance"
        and 220 <= len(text) <= 1800
        and bool(record.get("chunk_id"))
        and bool(record.get("source_id"))
        and bool(record.get("committee"))
        and bool(record.get("meeting_date"))
        and bool(record.get("speaker"))
        and not PROCEDURAL.search(text)
    )


def load_chunks(root: Path) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for path in sorted(root.rglob("chunks_v1.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                record = json.loads(raw)
                if eligible(record):
                    chunks.append(record)
    return chunks


def excluded_source_ids(dataset: Path | list[Path] | None) -> set[str]:
    if dataset is None:
        return set()
    datasets = [dataset] if isinstance(dataset, Path) else dataset
    excluded: set[str] = set()
    for path in datasets:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            record = json.loads(raw)
            if record.get("source_id"):
                excluded.add(record["source_id"])
            for source_id in record.get("target_source_ids", []):
                if source_id:
                    excluded.add(source_id)
            for evidence in record.get("gold", {}).get("evidence", []):
                source_id = evidence.get("source_id")
                if source_id:
                    excluded.add(source_id)
    return excluded


def sample_balanced(chunks: list[dict[str, Any]], count: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        groups[chunk["committee"]].append(chunk)
    for values in groups.values():
        rng.shuffle(values)

    committees = sorted(groups)
    rng.shuffle(committees)
    picked: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    while len(picked) < count:
        progressed = False
        for committee in committees:
            values = groups[committee]
            while values and values[-1]["source_id"] in seen_sources:
                values.pop()
            if not values:
                continue
            chunk = values.pop()
            picked.append(chunk)
            seen_sources.add(chunk["source_id"])
            progressed = True
            if len(picked) == count:
                break
        if not progressed:
            break
    return picked


def candidate(chunk: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "candidate_id": f"candidate-{index:03d}",
        "chunk_id": chunk["chunk_id"],
        "source_id": chunk["source_id"],
        "speaker": chunk.get("speaker"),
        "role": chunk.get("role"),
        "committee": chunk["committee"],
        "committee_filter": chunk.get("folder") or chunk["committee"],
        "date": chunk["meeting_date"],
        "page_start": chunk.get("page_start"),
        "text": chunk["text"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--exclude-dataset", type=Path, action="append", default=[])
    args = parser.parse_args()
    chunks = load_chunks(args.chunks)
    excluded = excluded_source_ids(args.exclude_dataset)
    chunks = [chunk for chunk in chunks if chunk["source_id"] not in excluded]
    selected = sample_balanced(chunks, args.count, args.seed)
    if len(selected) != args.count:
        raise SystemExit(f"후보 부족: 요청 {args.count}, 추출 {len(selected)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for index, chunk in enumerate(selected, start=1):
            handle.write(json.dumps(candidate(chunk, index), ensure_ascii=False) + "\n")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(json.dumps({
        "status": "PASS",
        "eligible_chunks": len(chunks),
        "selected": len(selected),
        "seed": args.seed,
        "excluded_sources": len(excluded),
        "sha256": digest,
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
