"""
[마지막] pipeline_report
파이프라인 전 단계 산출물 현황을 집계해 요약 리포트를 출력한다.

실행:
    python scripts/pipeline_report.py
"""

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DATA_ROOT = Path(__file__).parent.parent / "data" / "v1"


def count_dir(path: Path, filename: str) -> tuple[int, int]:
    """(존재하는 source_id 수, 총 row 수) 반환."""
    if not path.exists():
        return 0, 0
    sources = 0
    rows    = 0
    for jsonl in path.glob(f"*/{filename}"):
        sources += 1
        with open(jsonl, encoding="utf-8") as f:
            rows += sum(1 for line in f if line.strip())
    return sources, rows


def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    extract_src,   extract_rows   = count_dir(DATA_ROOT / "extract",   "pages.jsonl")
    normalize_src, normalize_rows = count_dir(DATA_ROOT / "normalized", "normalized.jsonl")
    parsed_src,    parsed_rows    = count_dir(DATA_ROOT / "parsed",     "turns.jsonl")
    enriched_src,  enriched_rows  = count_dir(DATA_ROOT / "enriched",   "enriched_turns.jsonl")
    chunks_src,    chunks_rows    = count_dir(DATA_ROOT / "chunks",     "chunks_v1.jsonl")

    report = {
        "generated_at":  now,
        "pipeline":      "National Assembly Minutes DataOps v1",
        "stages": {
            "extract":   {"sources": extract_src,   "pages":         extract_rows},
            "normalize": {"sources": normalize_src, "pages":         normalize_rows},
            "parse":     {"sources": parsed_src,    "turns":         parsed_rows},
            "enrich":    {"sources": enriched_src,  "enriched_turns":enriched_rows},
            "chunk":     {"sources": chunks_src,    "chunks":        chunks_rows},
        },
        "completion": {
            "extract_rate":   f"{extract_src}/{extract_src or '?'}",
            "normalize_rate": f"{normalize_src}/{extract_src or '?'}",
            "parse_rate":     f"{parsed_src}/{normalize_src or '?'}",
            "enrich_rate":    f"{enriched_src}/{parsed_src or '?'}",
            "chunk_rate":     f"{chunks_src}/{enriched_src or '?'}",
        },
    }

    print("=" * 60)
    print("  National Assembly Minutes DataOps — Pipeline Report")
    print(f"  {now}")
    print("=" * 60)
    print(f"  [extract]   {extract_src:>4}개 source  /  {extract_rows:>8,}페이지")
    print(f"  [normalize] {normalize_src:>4}개 source  /  {normalize_rows:>8,}페이지")
    print(f"  [parse]     {parsed_src:>4}개 source  /  {parsed_rows:>8,}턴")
    print(f"  [enrich]    {enriched_src:>4}개 source  /  {enriched_rows:>8,}턴")
    print(f"  [chunk]     {chunks_src:>4}개 source  /  {chunks_rows:>8,}청크")
    print("=" * 60)

    # JSON 리포트 저장
    report_dir = DATA_ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = report_dir / f"pipeline_report_{ts}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n리포트 저장: {out}")


if __name__ == "__main__":
    main()
