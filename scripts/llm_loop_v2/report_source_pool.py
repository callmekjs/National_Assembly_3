"""블라인드 원문 후보 풀의 분포와 수동 검토 우선 항목을 Markdown으로 만든다."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


RISK_PATTERNS = {
    "procedural": re.compile(r"(개의하겠습니다|산회를 선포|상정합니다|정회를 선포)"),
    "truncated_mic": re.compile(r"(마이크 중단|발언시간 초과)"),
    "oath": re.compile(r"(선서,|위증의 벌|맹서합니다)"),
    "context_heavy": re.compile(r"(그거|이것|그 부분|이 부분|아까|조금 전)"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = [json.loads(line) for line in args.pool.read_text(encoding="utf-8").splitlines() if line.strip()]
    committees = Counter(record["committee"] for record in records)
    roles = Counter(record.get("role") or "역할 없음" for record in records)
    flags: list[tuple[str, str, str]] = []
    flag_counts = Counter()
    for record in records:
        text = record["text"]
        matched = [name for name, pattern in RISK_PATTERNS.items() if pattern.search(text)]
        for name in matched:
            flag_counts[name] += 1
        if matched:
            flags.append((record["candidate_id"], ", ".join(matched), text[:120].replace("\n", " ")))

    lines = [
        "# 블라인드 원문 후보 풀 점검",
        "",
        f"- 전체 후보: {len(records)}개",
        f"- 고유 회의: {len({r['source_id'] for r in records})}개",
        f"- 날짜 범위: {min(r['date'] for r in records)} ~ {max(r['date'] for r in records)}",
        "- 이 파일은 질문·정답이 아니라 수동 작성 전 원문 후보 점검표다.",
        "",
        "## 위원회 분포",
        "",
        "| 위원회 | 후보 수 |",
        "|---|---:|",
        *[f"| {name} | {count} |" for name, count in sorted(committees.items())],
        "",
        "## 역할 상위 15개",
        "",
        "| 역할 | 후보 수 |",
        "|---|---:|",
        *[f"| {name} | {count} |" for name, count in roles.most_common(15)],
        "",
        "## 수동 검토 경보",
        "",
        "| 경보 | 건수 |",
        "|---|---:|",
        *[f"| {name} | {flag_counts[name]} |" for name in RISK_PATTERNS],
        "",
        "경보가 있다고 바로 제외하지 않는다. 문맥 의존·진행 발언·잘린 발언인지 원문을 직접 확인한다.",
        "",
        "| 후보 | 경보 | 미리보기 |",
        "|---|---|---|",
        *[f"| {candidate_id} | {risk} | {preview.replace('|', '/')} |" for candidate_id, risk, preview in flags],
        "",
    ]
    args.output.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(json.dumps({"status": "PASS", "records": len(records), "flagged": len(flags)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
