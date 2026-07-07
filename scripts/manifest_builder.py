"""
[0] manifest_builder
incoming_data/{위원회}/*.pdf를 스캔해 처리 대상 목록을 만든다.
출력: data/v2/manifest.jsonl

실행:
    python scripts/manifest_builder.py
"""

import hashlib
import json
import re
import sys
from pathlib import Path

# 위원회 정의는 공용 모듈 단일 출처 (committees.py)
from committees import FOLDER_TO_COMMITTEE

INPUT_ROOT  = Path(__file__).parent.parent / "incoming_data"
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "v2" / "manifest.jsonl"


def extract_date_hint(file_name: str) -> str:
    """파일명 접두사 YYYYMMDD에서 날짜를 추출한다."""
    m = re.match(r"(\d{8})", file_name)
    return m.group(1) if m else ""


def file_hash(path: Path) -> str:
    """SHA-256 해시를 반환한다."""
    # TODO: 대용량 파일은 청크 단위로 읽어야 함
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def make_source_id(folder: str, file_name: str) -> str:
    """source_id = 폴더명_파일명(확장자 제외)"""
    stem = Path(file_name).stem
    return f"{folder}_{stem}"


def scan_pdfs() -> list[dict]:
    """incoming_data 아래 PDF를 전부 스캔해 manifest 행 목록을 반환한다."""
    rows = []
    # TODO: 병렬 처리로 해시 계산 속도 개선 고려
    for folder, committee in FOLDER_TO_COMMITTEE.items():
        folder_path = INPUT_ROOT / folder
        if not folder_path.exists():
            print(f"[SKIP] 폴더 없음: {folder_path}", file=sys.stderr)
            continue
        for pdf_path in sorted(folder_path.glob("*.pdf")):
            try:
                rows.append({
                    "source_id":  make_source_id(folder, pdf_path.name),
                    "committee":  committee,
                    "folder":     folder,
                    "file_name":  pdf_path.name,
                    "file_path":  str(pdf_path),
                    "file_hash":  file_hash(pdf_path),
                    "file_size":  pdf_path.stat().st_size,
                    "date_hint":  extract_date_hint(pdf_path.name),
                    "status":     "pending",
                })
            except Exception as exc:
                print(f"[ERROR] {pdf_path}: {exc}", file=sys.stderr)
                rows.append({
                    "source_id": make_source_id(folder, pdf_path.name),
                    "committee": committee,
                    "folder":    folder,
                    "file_name": pdf_path.name,
                    "file_path": str(pdf_path),
                    "file_hash": None,
                    "file_size": None,
                    "date_hint": extract_date_hint(pdf_path.name),
                    "status":    "error",
                })
    return rows


def main() -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    rows = scan_pdfs()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"manifest 완료: {len(rows)}개 → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
