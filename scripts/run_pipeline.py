"""
[실행기] run_pipeline
파일 파이프라인(extract→normalize→parse→gate→enrich→chunk→gate)을 순서대로 실행한다.
게이트가 실패하면 그 자리에서 중단한다 — 손 순서 실수 방지용 얇은 실행기.

실행:
    python scripts/run_pipeline.py                       # 전체 (기존 산출물 있으면 각 단계가 스킵)
    python scripts/run_pipeline.py --from parse --clean  # parse부터 재생성 (산출물 삭제 후)
    python scripts/run_pipeline.py --from parse --clean 기재위   # 특정 위원회만

주의:
    - --clean 은 --from 단계부터 하류의 파생 산출물만 삭제한다.
    - extract 층(원본 추출)은 --clean-extract 를 명시해야만 삭제한다 (재추출 비용 큼).
    - DB 적재·임베딩은 비용이 들므로 여기 포함하지 않는다. 종료 시 다음 명령을 안내한다.
"""

import io
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent
SCRIPTS      = PROJECT_ROOT / "scripts"
DATA         = PROJECT_ROOT / "data" / "v1"

# (단계명, 스크립트, 산출물 디렉토리(청소 대상), 필터 인자 지원 여부)
STAGES = [
    ("extract",     "extractor_v1.py",        DATA / "extract",                  True),
    ("normalize",   "normalizer_v1.py",       DATA / "normalized",               True),
    ("parse",       "parser_v1.py",           DATA / "parsed",                   True),
    ("turns_gate",  "turns_quality_gate.py",  DATA / "reports" / "turns_quality", True),
    ("enrich",      "policy_enricher_v1.py",  DATA / "enriched",                 True),
    ("chunk",       "chunker_v1.py",          DATA / "chunks",                   True),
    ("chunks_gate", "chunks_quality_gate.py", None,                              False),  # --all 고정
]

STAGE_NAMES = [s[0] for s in STAGES]


def main() -> None:
    args = sys.argv[1:]

    start_stage = "extract"
    if "--from" in args:
        idx = args.index("--from")
        start_stage = args[idx + 1]
        if start_stage not in STAGE_NAMES:
            print(f"[ERROR] 알 수 없는 단계: {start_stage}  (가능: {', '.join(STAGE_NAMES)})")
            sys.exit(1)
        del args[idx:idx + 2]

    clean         = "--clean" in args
    clean_extract = "--clean-extract" in args
    args = [a for a in args if a not in ("--clean", "--clean-extract")]
    targets = args  # 남은 인자 = 위원회 필터 (예: 기재위)

    start_idx = STAGE_NAMES.index(start_stage)
    to_run = STAGES[start_idx:]

    # ── 청소 ────────────────────────────────────────────────────────────────
    if clean:
        if targets:
            print("[ERROR] --clean 은 위원회 필터와 함께 쓸 수 없다 (전체 삭제라서).")
            print("        특정 위원회만 재처리하려면 해당 source 폴더를 직접 지우고 실행할 것.")
            sys.exit(1)
        for name, _, out_dir, _ in to_run:
            if out_dir is None or not out_dir.exists():
                continue
            if name == "extract" and not clean_extract:
                print(f"  [보호] {out_dir} 는 --clean-extract 없이는 삭제하지 않음 (재추출 비용 큼)")
                continue
            shutil.rmtree(out_dir)
            print(f"  [삭제] {out_dir}")
    else:
        # 산출물이 이미 있으면 각 단계가 스킵함을 경고
        stale = [str(d) for _, _, d, _ in to_run if d and d.exists()]
        if stale:
            print("[주의] 기존 산출물이 있어 해당 단계는 스킵될 수 있다. 재생성하려면 --clean 사용.")

    # ── 실행 ────────────────────────────────────────────────────────────────
    t0 = time.time()
    for name, script, _, accepts_filter in to_run:
        cmd = [sys.executable, str(SCRIPTS / script)]
        if name == "chunks_gate":
            cmd.append("--all")
        elif accepts_filter and targets:
            cmd += targets

        print(f"\n=== [{name}] {' '.join(cmd[1:])} ===")
        result = subprocess.run(cmd, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            print(f"\n[중단] {name} 단계 실패 (exit {result.returncode}). 이후 단계를 실행하지 않는다.")
            sys.exit(result.returncode)

    print(f"\n{'=' * 56}")
    print(f"  파일 파이프라인 완료 — 소요 {(time.time() - t0) / 60:.1f}분")
    print(f"{'=' * 56}")
    print("다음 단계 (비용/시간이 들어 자동 실행하지 않음):")
    print("  1. python scripts/jsonl_to_postgres.py     # DB 재적재")
    print("  2. python scripts/embeddings_v1.py --dry-run  # 임베딩 비용 확인")
    print("  3. python scripts/embeddings_v1.py         # 임베딩 (과금 발생)")
    print("  4. python scripts/etl_audit.py 300         # 원본 대조 감사")


if __name__ == "__main__":
    main()
