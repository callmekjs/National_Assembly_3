"""
품질 게이트 자체를 검증하는 테스트 ("게이트의 게이트").

배경: chunks_quality_gate --all 의 경로가 data/v2 오타로 되어 있어
아무것도 검사하지 않고 통과하던 사고가 있었다 (2026-07-02 발견).
게이트가 고장나면 나쁜 데이터가 통과하므로, 게이트 동작 자체를 검증한다.

검사 항목:
    1. chunks_quality_gate --all 이 실제 파일을 1개 이상 검사하는가 (무검사 통과 방지)
    2. 나쁜 청크 파일을 주면 BLOCK(exit 1) 하는가
    3. 좋은 청크 파일을 주면 PASS(exit 0) 하는가
    4. turns_quality_gate 가 실제 source 를 검사해 리포트를 남기는가

실행: python tests/test_quality_gates.py
"""

import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent
SCRIPTS      = PROJECT_ROOT / "scripts"
CHUNKS_ROOT  = PROJECT_ROOT / "data" / "v1" / "chunks"

PY = sys.executable


def run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def real_source_id() -> str:
    """게이트의 source 추적 검사를 통과하려면 실존하는 source_id 가 필요하다."""
    if CHUNKS_ROOT.exists():
        dirs = sorted(p.name for p in CHUNKS_ROOT.iterdir() if p.is_dir())
        if dirs:
            return dirs[0]
    return "테스트_20240101_1_1"  # 데이터 없으면 가짜 (테스트 [3] 스킵됨)


GOOD_CHUNK = {
    "chunk_id": "테스트_turn_0001_chunk_001",
    "turn_id": "테스트_turn_0001",
    "chunk_type": "utterance",
    "source_id": None,  # main() 에서 실존 source_id 로 채움
    "committee": "테스트위원회",
    "meeting_date": "2024-01-01",
    "speaker": "홍길동",
    "role": "위원",
    "file_name": "20240101_1_1.pdf",
    "page_start": 1,
    "page_end": 1,
    "text": (
        "이것은 게이트 테스트용 정상 발언입니다. 충분한 길이의 본문 텍스트를 가지고 있으며 "
        "형식도 올바릅니다. 품질 게이트의 짧은 청크 경고 기준(100자)을 넘기기 위해 "
        "문장을 조금 더 길게 작성해 둡니다. 실제 회의록 발언과 유사한 길이입니다."
    ),
}


def main() -> None:
    failed = 0
    GOOD_CHUNK["source_id"] = real_source_id()

    # ── 1. --all 이 실제로 검사하는가 (무검사 통과 재발 방지) ────────────────
    code, out = run([PY, str(SCRIPTS / "chunks_quality_gate.py"), "--all"])
    n_files = len(list(CHUNKS_ROOT.glob("*/chunks_v1.jsonl"))) if CHUNKS_ROOT.exists() else 0
    checked = ("검사할 파일 없음" not in out) and ("총 " in out)
    ok = checked and n_files > 0
    print(f"  {'✓' if ok else '✗'} [1] --all 실검사 여부: 파일 {n_files}개 존재, 검사 수행됨={checked}")
    if not ok:
        failed += 1

    # ── 2. 나쁜 파일 → BLOCK (exit 1) ──────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        bad_path = Path(td) / "chunks_v1.jsonl"
        bad = dict(GOOD_CHUNK)
        bad["meeting_date"] = None          # 필수 메타 결측
        bad["text"] = ""                    # 빈 본문
        bad2 = dict(GOOD_CHUNK)             # chunk_id 중복
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(bad, ensure_ascii=False) + "\n")
            f.write(json.dumps(bad2, ensure_ascii=False) + "\n")
            f.write(json.dumps(bad2, ensure_ascii=False) + "\n")
        code, out = run([PY, str(SCRIPTS / "chunks_quality_gate.py"), str(bad_path)])
        ok = code == 1
        print(f"  {'✓' if ok else '✗'} [2] 나쁜 청크 파일 → BLOCK: exit={code} (기대 1)")
        if not ok:
            failed += 1

    # ── 3. 좋은 파일 → PASS (exit 0) ───────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        good_path = Path(td) / "chunks_v1.jsonl"
        with open(good_path, "w", encoding="utf-8") as f:
            for i in range(1, 11):
                c = dict(GOOD_CHUNK)
                c["chunk_id"] = f"테스트_turn_{i:04d}_chunk_001"
                c["turn_id"] = f"테스트_turn_{i:04d}"
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        code, out = run([PY, str(SCRIPTS / "chunks_quality_gate.py"), str(good_path)])
        ok = code == 0
        print(f"  {'✓' if ok else '✗'} [3] 정상 청크 파일 → PASS: exit={code} (기대 0)")
        if not ok:
            failed += 1

    # ── 4. turns_quality_gate 가 실검사·리포트 생성하는가 ────────────────────
    report_dir = PROJECT_ROOT / "data" / "v1" / "reports" / "turns_quality"
    parsed_dir = PROJECT_ROOT / "data" / "v1" / "parsed"
    if parsed_dir.exists():
        sample = sorted(p.name for p in parsed_dir.iterdir() if p.is_dir())[:1]
        if sample:
            code, out = run([PY, str(SCRIPTS / "turns_quality_gate.py"), "--source", sample[0]])
            report = report_dir / sample[0] / "turns_quality_report.json"
            ok = code == 0 and report.exists()
            print(f"  {'✓' if ok else '✗'} [4] turns_gate 실검사+리포트: exit={code}, report={report.exists()}")
            if not ok:
                failed += 1
        else:
            print("  - [4] parsed 데이터 없음 — 건너뜀")
    else:
        print("  - [4] parsed 디렉토리 없음 — 건너뜀")

    total = 4
    print(f"\n결과: {total - failed}/{total} 통과" + ("" if failed == 0 else "  ← 실패!"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
