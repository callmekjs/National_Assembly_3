"""
[4] turns_quality_gate
parser_v1 산출물(turns.jsonl)의 품질을 검사한다.
BLOCK 항목이 하나라도 발생하면 종료 코드 1로 끝난다.

실행:
    python scripts/turns_quality_gate.py                    # data/v1/parsed 전체
    python scripts/turns_quality_gate.py 과방위 외통위        # 특정 위원회만
    python scripts/turns_quality_gate.py --source 과방위_20240611_52074_52074
"""

import io
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

INPUT_ROOT  = Path(__file__).parent.parent / "data" / "v1" / "parsed"
REPORT_ROOT = Path(__file__).parent.parent / "data" / "v1" / "reports" / "turns_quality"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MARKER_RE = re.compile(r"^[◯○◎]")

_NON_SPEAKER = frozenset([
    "출석", "정부측", "기타", "참석자", "의안", "보고사항", "부록",
    "행정입법", "계획서", "보고서", "예비심사기간", "청원",
])

SHORT_THRESHOLD = 20    # 자
SHORT_RATE_WARN = 0.20  # 20% 이상 → WARNING


@dataclass
class CheckResult:
    name:    str
    status:  str   # OK / BLOCK / WARNING
    value:   object = None
    message: str = ""


@dataclass
class TurnsReport:
    source_id:     str
    total:         int
    result:        str = "OK"
    checks:        list[CheckResult] = field(default_factory=list)
    block_reasons: list[str]         = field(default_factory=list)
    warnings:      list[str]         = field(default_factory=list)


def _add(report: TurnsReport, name: str, ok: bool, value: object,
         message: str, severity: str = "BLOCK") -> None:
    if ok:
        report.checks.append(CheckResult(name=name, status="OK", value=value))
    else:
        cr = CheckResult(name=name, status=severity, value=value, message=message)
        report.checks.append(cr)
        if severity == "BLOCK":
            report.block_reasons.append(message)
        else:
            report.warnings.append(message)


def run_checks(turns: list[dict], source_id: str) -> TurnsReport:
    report = TurnsReport(source_id=source_id, total=len(turns))
    n = len(turns)

    # BLOCK: turn 수 0
    if n == 0:
        report.result = "BLOCK"
        report.block_reasons.append("turn이 0개")
        return report

    # BLOCK: speaker 누락률 1% 이상
    missing_spk = sum(1 for t in turns if not t.get("speaker", "").strip())
    rate = missing_spk / n
    _add(report, "speaker_missing_rate", rate < 0.01, f"{rate:.1%}",
         f"speaker 누락 {missing_spk}건 ({rate:.1%}) — 기준 1% 초과")

    # BLOCK: meeting_date 누락 또는 형식 오류
    bad_date = [t for t in turns
                if not t.get("meeting_date") or not _DATE_RE.match(str(t["meeting_date"]))]
    _add(report, "meeting_date_format", len(bad_date) == 0, len(bad_date),
         f"meeting_date 오류 {len(bad_date)}건 (누락 또는 YYYY-MM-DD 형식 아님)")

    # BLOCK: turn_id 중복
    ids = [t.get("turn_id") for t in turns if t.get("turn_id")]
    dup = len(ids) - len(set(ids))
    _add(report, "turn_id_duplicate", dup == 0, dup,
         f"turn_id 중복 {dup}건")

    # BLOCK: page_start > page_end
    bad_page = sum(1 for t in turns
                   if t.get("page_start") is not None and t.get("page_end") is not None
                   and t["page_start"] > t["page_end"])
    _add(report, "page_range_invalid", bad_page == 0, bad_page,
         f"page_start > page_end인 turn {bad_page}건")

    # BLOCK: 비발언 항목이 speaker로 남아 있음
    bad_spk = [t for t in turns if t.get("speaker", "") in _NON_SPEAKER]
    _add(report, "non_speaker_leaked", len(bad_spk) == 0, len(bad_spk),
         f"비발언 항목이 speaker로 남은 turn {len(bad_spk)}건: "
         f"{list({t['speaker'] for t in bad_spk})[:5]}")

    # BLOCK: source_id / committee / file_name 누락
    missing_meta = sum(
        1 for t in turns
        if not t.get("source_id") or not t.get("committee") or not t.get("file_name")
    )
    _add(report, "required_meta_missing", missing_meta == 0, missing_meta,
         f"source_id / committee / file_name 누락 {missing_meta}건")

    # WARNING: ◯ 마커가 text 앞에 남아 있음
    marker_left = sum(1 for t in turns if _MARKER_RE.match(t.get("text", "")))
    _add(report, "marker_in_text", marker_left == 0, marker_left,
         f"◯/◎ 마커가 text 앞에 남은 turn {marker_left}건", severity="WARNING")

    # WARNING: 20자 미만 turn 비율 20% 이상
    short = sum(1 for t in turns if len(t.get("text", "")) < SHORT_THRESHOLD)
    short_rate = short / n
    _add(report, "short_turn_rate", short_rate < SHORT_RATE_WARN, f"{short_rate:.1%}",
         f"20자 미만 turn {short}건 ({short_rate:.1%}) — 기준 {SHORT_RATE_WARN:.0%} 초과",
         severity="WARNING")

    report.result = "BLOCK" if report.block_reasons else "OK"
    return report


def save_report(report: TurnsReport, source_id: str) -> Path:
    out_dir = REPORT_ROOT / source_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "turns_quality_report.json"
    payload = {
        "source_id":     report.source_id,
        "total_turns":   report.total,
        "result":        report.result,
        "block_reasons": report.block_reasons,
        "warnings":      report.warnings,
        "checks":        [
            {"name": c.name, "status": c.status, "value": c.value,
             "message": c.message or None}
            for c in report.checks
        ],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path


def check_source(source_id: str) -> bool:
    turns_path = INPUT_ROOT / source_id / "turns.jsonl"

    if not turns_path.exists():
        print(f"  [BLOCK] {source_id}: turns.jsonl 없음")
        return False

    turns = []
    try:
        with open(turns_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    turns.append(json.loads(line))
    except Exception as exc:
        print(f"  [BLOCK] {source_id}: JSONL 파싱 실패 — {exc}")
        return False

    report  = run_checks(turns, source_id)
    out     = save_report(report, source_id)
    label   = "PASS" if report.result == "OK" else "BLOCK"

    print(f"  [{label}] {source_id}  ({report.total}턴)")
    for r in report.block_reasons:
        print(f"    ✗ {r}")
    for w in report.warnings:
        print(f"    ! {w}")

    return report.result == "OK"


def main() -> None:
    args = sys.argv[1:]

    # --source {source_id}
    if args and args[0] == "--source":
        passed = check_source(args[1])
        sys.exit(0 if passed else 1)

    # 위원회 필터 (접두사 매칭)
    targets = set(args)
    source_ids = sorted(p.name for p in INPUT_ROOT.iterdir() if p.is_dir())
    if targets:
        source_ids = [s for s in source_ids if any(s.startswith(t) for t in targets)]

    if not source_ids:
        print("검사할 source_id 없음")
        sys.exit(0)

    results = [check_source(sid) for sid in source_ids]
    blocked = results.count(False)
    print(f"\n총 {len(results)}개 — PASS {results.count(True)} / BLOCK {blocked}")
    print(f"리포트 위치: {REPORT_ROOT}")
    sys.exit(1 if blocked else 0)


if __name__ == "__main__":
    main()
