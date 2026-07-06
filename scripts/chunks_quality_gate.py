"""
[6] chunks_quality_gate
chunks_v1.jsonl을 검사해 PostgreSQL 적재 가능 여부를 판정한다.
BLOCK 항목이 하나라도 발생하면 종료 코드 1로 끝난다.

실행:
    python scripts/chunks_quality_gate.py data/v1/chunks/{source_id}/chunks_v1.jsonl
    python scripts/chunks_quality_gate.py --all   # data/v1/chunks 아래 전체 검사
"""

import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── 임계값 ────────────────────────────────────────────────────────────────────
THRESHOLDS = {
    "meeting_date_null_rate":  ("BLOCK",   0.05),   # 5% 이상 → 중단
    "speaker_missing_rate":    ("BLOCK",   0.10),   # 10% 이상 → 중단
    "committee_missing_any":   ("BLOCK",   0),      # 1건이라도 → 중단
    "empty_text_rate":         ("BLOCK",   0.05),   # 5% 이상 → 중단
    "short_chunk_rate":        ("WARNING", 0.20),   # 20% 이상 → 경고
    "long_chunk_rate":         ("WARNING", 0.05),   # 5% 이상 → 경고
    "duplicate_chunk_id_any":  ("BLOCK",   0),      # 1건이라도 → 중단
    "low_marker_detection":    ("WARNING", None),   # parser 단계 리포트용
    "chunk_type_invalid_any":  ("BLOCK",   0),      # 1건이라도 → 중단
    "source_missing_rate":     ("BLOCK",   0.01),   # 1% 이상 → 중단
}

VALID_CHUNK_TYPES = {"utterance", "qa_pair"}

SHORT_THRESHOLD = 100   # 자
LONG_THRESHOLD  = 3000  # 자
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    check_id:  int
    name:      str
    value:     float | int | None
    threshold: float | int | None
    status:    str          # OK / BLOCK / WARNING
    message:   str = ""


@dataclass
class QualityReport:
    source_id: str
    total:     int
    result:    str = "OK"   # OK / BLOCK
    checks:    list[CheckResult] = field(default_factory=list)
    warnings:  list[CheckResult] = field(default_factory=list)
    block_reasons: list[str] = field(default_factory=list)


def load_chunks(path: Path) -> list[dict]:
    chunks = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def run_checks(chunks: list[dict], source_id: str) -> QualityReport:
    report = QualityReport(source_id=source_id, total=len(chunks))
    n = len(chunks)
    if n == 0:
        report.result = "BLOCK"
        report.block_reasons.append("청크가 0개 — 빈 파일")
        return report

    # ── 각 항목 계산 ──────────────────────────────────────────────────────────

    # 1. meeting_date null 비율
    null_date = sum(1 for c in chunks if not c.get("meeting_date"))
    _add_check(report, 1, "meeting_date_null_rate", null_date / n, 0.05)

    # 2. speaker 누락 비율
    null_speaker = sum(1 for c in chunks if not c.get("speaker"))
    _add_check(report, 2, "speaker_missing_rate", null_speaker / n, 0.10)

    # 3. committee 누락 여부 (1건이라도)
    no_committee = sum(1 for c in chunks if not c.get("committee"))
    _add_check(report, 3, "committee_missing_any", no_committee, 0, count_mode=True)

    # 4. 빈 텍스트 청크 비율
    empty_text = sum(1 for c in chunks if not (c.get("text") or "").strip())
    _add_check(report, 4, "empty_text_rate", empty_text / n, 0.05)

    # 5. 너무 짧은 청크 비율 (WARNING)
    short = sum(1 for c in chunks if len((c.get("text") or "")) < SHORT_THRESHOLD)
    _add_check(report, 5, "short_chunk_rate", short / n, 0.20, severity="WARNING")

    # 6. 너무 긴 청크 비율 (WARNING)
    long_ = sum(1 for c in chunks if len((c.get("text") or "")) > LONG_THRESHOLD)
    _add_check(report, 6, "long_chunk_rate", long_ / n, 0.05, severity="WARNING")

    # 7. 중복 chunk_id (1건이라도)
    ids = [c.get("chunk_id") for c in chunks if c.get("chunk_id")]
    dup = len(ids) - len(set(ids))
    _add_check(report, 7, "duplicate_chunk_id_any", dup, 0, count_mode=True)

    # 8. PDF 마커 인식률 (WARNING — TODO: parser 단계에서 기록 필요)
    # TODO: turns.jsonl의 marker_found 필드를 읽어 계산
    report.warnings.append(CheckResult(
        check_id=8, name="low_marker_detection",
        value=None, threshold=None, status="SKIP",
        message="TODO: parser_v2 단계의 마커 인식률 데이터 필요",
    ))

    # 9. chunk_type 유효성 (1건이라도 잘못된 타입)
    invalid_type = sum(1 for c in chunks if c.get("chunk_type") not in VALID_CHUNK_TYPES)
    _add_check(report, 9, "chunk_type_invalid_any", invalid_type, 0, count_mode=True)

    # 10. source 추적 가능 여부
    source_fields = ("source_id", "file_name", "page_start", "page_end")
    no_source = sum(
        1 for c in chunks
        if any(c.get(f) is None for f in source_fields)
    )
    _add_check(report, 10, "source_missing_rate", no_source / n, 0.01)

    # ── 최종 판정 ─────────────────────────────────────────────────────────────
    report.result = "BLOCK" if report.block_reasons else "OK"
    return report


def _add_check(
    report: QualityReport,
    check_id: int,
    name: str,
    value: float | int,
    threshold: float | int,
    severity: str = "BLOCK",
    count_mode: bool = False,
) -> None:
    exceeded = (value > threshold) if not count_mode else (value > 0)
    if exceeded:
        status = severity
        pct = f"{value:.1%}" if not count_mode else f"{value}건"
        thr = f"{threshold:.0%}" if not count_mode else "0건 초과"
        message = f"{name}: {pct} — 기준 {thr} 초과"
        cr = CheckResult(check_id=check_id, name=name, value=value,
                         threshold=threshold, status=status, message=message)
        if severity == "BLOCK":
            report.checks.append(cr)
            report.block_reasons.append(message)
        else:
            report.warnings.append(cr)
    else:
        report.checks.append(CheckResult(
            check_id=check_id, name=name, value=value,
            threshold=threshold, status="OK",
        ))


def save_report(report: QualityReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "quality_report.json"

    def _cr_to_dict(cr: CheckResult) -> dict:
        d = {"check_id": cr.check_id, "name": cr.name,
             "value": cr.value, "threshold": cr.threshold, "status": cr.status}
        if cr.message:
            d["message"] = cr.message
        return d

    payload = {
        "source_id":    report.source_id,
        "total_chunks": report.total,
        "result":       report.result,
        "checks":       [_cr_to_dict(c) for c in report.checks],
        "warnings":     [_cr_to_dict(w) for w in report.warnings],
        "block_reason": "; ".join(report.block_reasons) if report.block_reasons else None,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path


def check_file(chunks_path: Path) -> bool:
    """단일 chunks_v1.jsonl을 검사한다. 통과하면 True, BLOCK이면 False."""
    source_id = chunks_path.parent.name
    chunks    = load_chunks(chunks_path)
    report    = run_checks(chunks, source_id)

    # 리포트 저장
    quality_dir = chunks_path.parent.parent.parent / "quality" / source_id
    out_path    = save_report(report, quality_dir)

    # 출력
    status_label = "PASS" if report.result == "OK" else "BLOCK"
    print(f"[{status_label}] {source_id}  ({report.total}청크)  → {out_path}")
    for reason in report.block_reasons:
        print(f"  ✗ {reason}")
    for w in report.warnings:
        if w.message:
            print(f"  ! {w.message}")

    return report.result == "OK"


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("사용법: chunks_quality_gate.py <chunks_v1.jsonl> | --all")
        sys.exit(1)

    if args[0] == "--all":
        chunks_root = Path(__file__).parent.parent / "data" / "v1" / "chunks"
        paths = list(chunks_root.glob("*/chunks_v1.jsonl"))
        if not paths:
            print("검사할 파일 없음")
            sys.exit(0)
        results = [check_file(p) for p in sorted(paths)]
        blocked = results.count(False)
        print(f"\n총 {len(results)}개 — 통과 {results.count(True)} / BLOCK {blocked}")
        sys.exit(1 if blocked else 0)
    else:
        path = Path(args[0])
        passed = check_file(path)
        sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
