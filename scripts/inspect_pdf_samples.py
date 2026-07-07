"""
PDF 샘플 검사 — 발언자 마커 패턴, 페이지 구조, 잡음 라인 확인
실행:
    python scripts/inspect_pdf_samples.py              # 위원회별 1개씩 자동 선택
    python scripts/inspect_pdf_samples.py path/to.pdf  # 특정 파일
"""

import io
import re
import sys
from collections import Counter
from pathlib import Path

import pdfplumber

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

INPUT_ROOT = Path(__file__).parent.parent / "incoming_data"

# 위원회 정의는 공용 모듈 단일 출처 (committees.py)
from committees import FOLDER_TO_COMMITTEE  # noqa: E402

# 확인할 마커 후보
MARKER_PATTERNS = [
    ("◯",  re.compile(r"◯")),
    ("○",  re.compile(r"○")),
    ("◎",  re.compile(r"◎")),
    ("●",  re.compile(r"●")),
    ("■",  re.compile(r"■")),
    ("▶",  re.compile(r"▶")),
]

# 잡음 패턴 (정규화 대상)
NOISE_PATTERNS = [
    ("페이지번호",   re.compile(r"^\s*\d+\s*$")),
    ("구분선",      re.compile(r"^[─━\-=○◯]{5,}$")),
    ("회의헤더",    re.compile(r"제\d+회.{0,20}(국회|위원회|소위)")),
    ("쪽표기",      re.compile(r"[-―]\s*\d+\s*[-―]")),
]


def inspect_pdf(pdf_path: Path) -> dict:
    result = {
        "file": pdf_path.name,
        "folder": pdf_path.parent.name,
        "pages": 0,
        "total_chars": 0,
        "marker_counts": {},
        "noise_line_counts": {},
        "sample_lines": [],       # 발언자 마커 포함 라인 샘플 (최대 5개)
        "first_page_preview": "",
        "empty_pages": 0,
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            result["pages"] = len(pdf.pages)
            marker_counts  = Counter()
            noise_counts   = Counter()
            sample_lines   = []

            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                result["total_chars"] += len(text)

                if not text.strip():
                    result["empty_pages"] += 1
                    continue

                if i == 0:
                    result["first_page_preview"] = text[:300]

                for line in text.splitlines():
                    # 마커 카운트
                    for name, pat in MARKER_PATTERNS:
                        if pat.search(line):
                            marker_counts[name] += 1
                            if len(sample_lines) < 5 and name in ("◯", "○", "◎"):
                                sample_lines.append(line.strip()[:80])

                    # 잡음 라인 카운트
                    for name, pat in NOISE_PATTERNS:
                        if pat.search(line):
                            noise_counts[name] += 1

            result["marker_counts"]     = dict(marker_counts)
            result["noise_line_counts"] = dict(noise_counts)
            result["sample_lines"]      = sample_lines

    except Exception as exc:
        result["error"] = str(exc)

    return result


def pick_samples() -> list[Path]:
    """각 위원회 폴더에서 가장 오래된 PDF 1개씩 선택."""
    samples = []
    for folder in FOLDER_TO_COMMITTEE:
        folder_path = INPUT_ROOT / folder
        pdfs = sorted(folder_path.glob("*.pdf")) if folder_path.exists() else []
        if pdfs:
            samples.append(pdfs[0])
    return samples


def print_report(results: list[dict]) -> None:
    print("\n" + "=" * 70)
    print("PDF 샘플 검사 결과")
    print("=" * 70)

    for r in results:
        print(f"\n[{r['folder']}] {r['file']}")
        if "error" in r:
            print(f"  ERROR: {r['error']}")
            continue

        print(f"  페이지 수: {r['pages']}  /  총 글자: {r['total_chars']:,}  /  빈 페이지: {r['empty_pages']}")

        # 마커
        mc = r["marker_counts"]
        if mc:
            marker_str = "  ".join(f"{k}:{v}" for k, v in sorted(mc.items(), key=lambda x: -x[1]))
            print(f"  마커 발견: {marker_str}")
        else:
            print(f"  마커 발견: 없음 ← 수동 확인 필요")

        # 잡음
        nc = r["noise_line_counts"]
        if nc:
            noise_str = "  ".join(f"{k}:{v}" for k, v in nc.items())
            print(f"  잡음 라인: {noise_str}")

        # 샘플 발언자 라인
        if r["sample_lines"]:
            print(f"  발언자 마커 샘플:")
            for line in r["sample_lines"]:
                print(f"    → {line}")

        # 첫 페이지 미리보기
        preview = r["first_page_preview"].replace("\n", " / ")[:120]
        print(f"  첫 페이지: {preview}")

    # 마커 종합
    print("\n" + "-" * 70)
    print("마커 종합 (전 위원회)")
    all_markers: Counter = Counter()
    for r in results:
        for k, v in r.get("marker_counts", {}).items():
            all_markers[k] += v
    if all_markers:
        for k, v in all_markers.most_common():
            print(f"  {k} : {v}회")
    else:
        print("  발견된 마커 없음")
    print("=" * 70 + "\n")


def main() -> None:
    if len(sys.argv) > 1:
        paths = [Path(a) for a in sys.argv[1:]]
    else:
        paths = pick_samples()
        print(f"위원회별 샘플 {len(paths)}개 자동 선택")

    results = []
    for p in paths:
        print(f"  검사 중: {p.name} ...", end=" ", flush=True)
        r = inspect_pdf(p)
        results.append(r)
        print("완료" if "error" not in r else f"오류: {r['error']}")

    print_report(results)


if __name__ == "__main__":
    main()
