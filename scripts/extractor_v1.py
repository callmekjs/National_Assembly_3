"""
[1] extractor_v1
pdfplumber로 PDF를 페이지별 텍스트로 추출한다.
출력: data/v1/extract/{source_id}/pages.jsonl

실행:
    python scripts/extractor_v1.py              # incoming_data 전체
    python scripts/extractor_v1.py 과방위 외통위  # 특정 위원회만
"""

EXTRACTOR_VERSION = "v1.0"

import io
import json
import re
import sys
import time
from pathlib import Path

import pdfplumber

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

INPUT_ROOT  = Path(__file__).parent.parent / "incoming_data"
OUTPUT_ROOT = Path(__file__).parent.parent / "data" / "v1" / "extract"

FOLDER_TO_COMMITTEE = {
    "과방위":    "과학기술정보방송통신위원회",
    "외통위":    "외교통일위원회",
    "정무위":    "정무위원회",
    "기재위":    "재정경제기획위원회",
    "행안위":    "행정안전위원회",
    "복지위":    "보건복지위원회",
    "국토위":    "국토교통위원회",
    "산자중기위": "산업통상자원중소벤처기업위원회",
    "국방위":    "국방위원회",
}


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


def save_pages(pages: list[dict], source_id: str) -> Path:
    out_dir = OUTPUT_ROOT / source_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "pages.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for p in pages:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    return out_path


def already_done(source_id: str) -> bool:
    return (OUTPUT_ROOT / source_id / "pages.jsonl").exists()


def process_folder(folder: str, committee: str) -> tuple[int, int]:
    folder_path = INPUT_ROOT / folder
    if not folder_path.exists():
        print(f"  [SKIP] 폴더 없음: {folder_path}")
        return 0, 0

    pdfs = sorted(folder_path.glob("*.pdf"))
    done = errors = 0

    for pdf_path in pdfs:
        source_id = make_source_id(folder, pdf_path.stem)
        if already_done(source_id):
            done += 1
            continue

        pages, err = extract_pdf(pdf_path, folder, committee)
        if err:
            print(f"  [오류] {pdf_path.name}: {err}")
            errors += 1
            continue

        save_pages(pages, source_id)
        print(f"  ↓ {pdf_path.name}  ({len(pages)}페이지)")
        done += 1
        time.sleep(0.05)  # 파일 I/O 부하 완화

    return done, errors


def main() -> None:
    targets = set(sys.argv[1:])

    total_done = total_errors = 0
    for folder, committee in FOLDER_TO_COMMITTEE.items():
        if targets and folder not in targets:
            continue
        print(f"\n[{folder}] {committee}")
        d, e = process_folder(folder, committee)
        total_done   += d
        total_errors += e
        print(f"  완료 {d} / 오류 {e}")

    print(f"\n추출 완료 — 총 {total_done}개 / 오류 {total_errors}개")
    print(f"출력 위치: {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
