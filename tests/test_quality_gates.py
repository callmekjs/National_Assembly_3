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

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
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


def _has_local_data() -> bool:
    return CHUNKS_ROOT.exists() and any(CHUNKS_ROOT.glob("*/chunks_v1.jsonl"))


def test_all_scans_real_files():
    """[1] --all 이 실제로 검사하는가 (무검사 통과 재발 방지). 로컬 데이터 없으면 건너뜀."""
    if not _has_local_data():
        print("  - [1] 로컬 청크 데이터 없음 — 건너뜀")
        return
    code, out = run([PY, str(SCRIPTS / "chunks_quality_gate.py"), "--all"])
    n_files = len(list(CHUNKS_ROOT.glob("*/chunks_v1.jsonl")))
    checked = ("검사할 파일 없음" not in out) and ("총 " in out)
    print(f"  {'✓' if checked else '✗'} [1] --all 실검사 여부: 파일 {n_files}개 존재, 검사 수행됨={checked}")
    assert checked, out[-500:]


def test_bad_file_blocks():
    """[2] 나쁜 파일 → BLOCK (exit 1)."""
    GOOD_CHUNK["source_id"] = real_source_id()
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
    print(f"  {'✓' if code == 1 else '✗'} [2] 나쁜 청크 파일 → BLOCK: exit={code} (기대 1)")
    assert code == 1, out[-500:]


def test_good_file_passes():
    """[3] 좋은 파일 → PASS (exit 0). source 추적 검사 때문에 실존 source_id 필요."""
    if not _has_local_data():
        print("  - [3] 로컬 청크 데이터 없음 (실존 source_id 불가) — 건너뜀")
        return
    GOOD_CHUNK["source_id"] = real_source_id()
    with tempfile.TemporaryDirectory() as td:
        good_path = Path(td) / "chunks_v1.jsonl"
        with open(good_path, "w", encoding="utf-8") as f:
            for i in range(1, 11):
                c = dict(GOOD_CHUNK)
                c["chunk_id"] = f"테스트_turn_{i:04d}_chunk_001"
                c["turn_id"] = f"테스트_turn_{i:04d}"
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        code, out = run([PY, str(SCRIPTS / "chunks_quality_gate.py"), str(good_path)])
    print(f"  {'✓' if code == 0 else '✗'} [3] 정상 청크 파일 → PASS: exit={code} (기대 0)")
    assert code == 0, out[-500:]


def test_turns_gate_reports():
    """[4] turns_quality_gate 가 실검사·리포트 생성하는가. 로컬 데이터 없으면 건너뜀."""
    report_dir = PROJECT_ROOT / "data" / "v1" / "reports" / "turns_quality"
    parsed_dir = PROJECT_ROOT / "data" / "v1" / "parsed"
    if not parsed_dir.exists():
        print("  - [4] parsed 디렉토리 없음 — 건너뜀")
        return
    sample = sorted(p.name for p in parsed_dir.iterdir() if p.is_dir())[:1]
    if not sample:
        print("  - [4] parsed 데이터 없음 — 건너뜀")
        return
    code, out = run([PY, str(SCRIPTS / "turns_quality_gate.py"), "--source", sample[0]])
    report = report_dir / sample[0] / "turns_quality_report.json"
    ok = code == 0 and report.exists()
    print(f"  {'✓' if ok else '✗'} [4] turns_gate 실검사+리포트: exit={code}, report={report.exists()}")
    assert ok, out[-500:]


def main() -> None:
    test_all_scans_real_files()
    test_bad_file_blocks()
    test_good_file_passes()
    test_turns_gate_reports()
    print("\n결과: 통과 (건너뜀 항목은 위 표시 참고)")


if __name__ == "__main__":
    main()
