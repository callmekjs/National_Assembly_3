"""
[1] extractor_v1
pdfplumber로 PDF를 페이지별 텍스트로 추출한다.
출력: data/v1/extract/{source_id}/pages.jsonl

실행:
    python scripts/extractor_v1.py              # incoming_data 전체
    python scripts/extractor_v1.py 과방위 외통위  # 특정 위원회만
"""

EXTRACTOR_VERSION = "v1.0"

import hashlib
import io
import re
import sys
import time
from pathlib import Path

import pdfplumber

from stage_io import report_failures, write_jsonl_atomic

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

INPUT_ROOT  = Path(__file__).parent.parent / "incoming_data"
OUTPUT_ROOT = Path(__file__).parent.parent / "data" / "v1" / "extract"

# 위원회 정의는 공용 모듈 단일 출처 (committees.py)
from committees import FOLDER_TO_COMMITTEE  # noqa: E402


def extract_date_hint(file_name: str) -> str:
    m = re.match(r"(\d{8})", file_name)
    return m.group(1) if m else ""


def make_source_id(folder: str, stem: str) -> str:
    return f"{folder}_{stem}"


def extract_pdf(pdf_path: Path, folder: str, committee: str) -> tuple[list[dict], str | None]:
    """PDF를 페이지별로 추출한다. (pages, error_msg) 반환."""
    stem      = pdf_path.stem
    source_id = make_source_id(folder, stem)
    date_hint = extract_date_hint(pdf_path.name)
    pages     = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                pages.append({
                    "source_id": source_id,
                    "committee": committee,
                    "folder":    folder,
                    "file_name": pdf_path.name,
                    "date_hint": date_hint,
                    "page":      i,
                    "text":      text,
                })
    except Exception as exc:
        return [], str(exc)

    if not any(p["text"].strip() for p in pages):
        return [], "전체 페이지 빈 텍스트"

    return pages, None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def save_pages(pages: list[dict], source_id: str, pdf_path: Path) -> Path:
    out_path = OUTPUT_ROOT / source_id / "pages.jsonl"
    write_jsonl_atomic(out_path, pages)  # 중단돼도 반쪽 파일이 남지 않게
    # 원본 PDF 지문 기록 — already_done 이 정정본(내용 바뀐 같은 이름 PDF)을 감지하는 근거
    (OUTPUT_ROOT / source_id / "source.sha256").write_text(_sha256(pdf_path), encoding="utf-8")
    return out_path


def already_done(source_id: str, pdf_path: Path) -> bool:
    """출력 존재 + 원본 PDF 해시 일치일 때만 완료 — 폴더 존재만 보면 국회가
    정정본을 올려도 옛 추출 결과가 영원히 쓰인다 (2026-07-07 검토 수정)."""
    out_path  = OUTPUT_ROOT / source_id / "pages.jsonl"
    hash_path = OUTPUT_ROOT / source_id / "source.sha256"
    if not out_path.exists():
        return False
    if not hash_path.exists():
        return True  # 해시 기록 없는 구버전 산출물 — 백필 스크립트로 채우기 전까지 기존 동작
    return hash_path.read_text(encoding="utf-8").strip() == _sha256(pdf_path)


def process_folder(folder: str, committee: str) -> tuple[int, list[tuple[str, str]]]:
    folder_path = INPUT_ROOT / folder
    if not folder_path.exists():
        print(f"  [SKIP] 폴더 없음: {folder_path}")
        return 0, []

    pdfs = sorted(folder_path.glob("*.pdf"))
    done = 0
    failures: list[tuple[str, str]] = []

    for pdf_path in pdfs:
        source_id = make_source_id(folder, pdf_path.stem)
        if already_done(source_id, pdf_path):
            done += 1
            continue

        pages, err = extract_pdf(pdf_path, folder, committee)
        if err:
            print(f"  [오류] {pdf_path.name}: {err}")
            failures.append((source_id, err))
            continue

        save_pages(pages, source_id, pdf_path)
        print(f"  ↓ {pdf_path.name}  ({len(pages)}페이지)")
        done += 1
        time.sleep(0.05)  # 파일 I/O 부하 완화

    return done, failures


def main() -> None:
    targets = set(sys.argv[1:])

    total_done = 0
    failures: list[tuple[str, str]] = []
    for folder, committee in FOLDER_TO_COMMITTEE.items():
        if targets and folder not in targets:
            continue
        print(f"\n[{folder}] {committee}")
        d, fails = process_folder(folder, committee)
        total_done += d
        failures.extend(fails)
        print(f"  완료 {d} / 오류 {len(fails)}")

    print(f"\n추출 완료 — 총 {total_done}개 / 오류 {len(failures)}개")
    print(f"출력 위치: {OUTPUT_ROOT}")
    fail_path = report_failures("extractor", failures)
    if fail_path:
        print(f"실패 목록: {fail_path}")
        sys.exit(1)  # run_pipeline 이 감지해 멈추도록


if __name__ == "__main__":
    main()
