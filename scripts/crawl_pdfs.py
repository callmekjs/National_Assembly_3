"""
국회 회의록 PDF 크롤러
- 대상: 제22대 국회 (2024-05-30~현재) 9개 상임위원회
- 저장: incoming_data/{위원회폴더명}/*.pdf

실행:
    python scripts/crawl_pdfs.py             # 전체 위원회
    python scripts/crawl_pdfs.py 과방위 기재위  # 특정 위원회만
"""

import io
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── 설정 ──────────────────────────────────────────────────────────────────────
BASE_URL = "https://www.assembly.go.kr"
LIST_API = f"{BASE_URL}/portal/cnts/cntsCmmit/listMtgRcord.json"

# (폴더명: (위원회 전체명, committeeCd))
# committeeCd는 API 응답 cmmnCdList에서 확인한 실제 코드
COMMITTEES = {
    "과방위":    ("과학기술정보방송통신위원회",       "9700479"),
    "외통위":    ("외교통일위원회",                "9700409"),
    "정무위":    ("정무위원회",                    "9700008"),
    "기재위":    ("재정경제기획위원회",              "9700590"),
    "행안위":    ("행정안전위원회",                "9700480"),
    "복지위":    ("보건복지위원회",                "9700341"),
    "국토위":    ("국토교통위원회",                "9700407"),
    "산자중기위": ("산업통상자원중소벤처기업위원회",   "9700481"),
    "국방위":    ("국방위원회",                    "9700019"),
}

CSRF_MENU_NO = "600238"    # CSRF 발급 전용 (API 필터링과 무관)
BEGIN_DATE   = "20240530"  # 제22대 국회 개원일
END_DATE     = ""          # 빈 문자열 = 현재까지
PAGE_SIZE    = 100
DELAY        = 0.5         # 요청 간 대기(초)
OUTPUT_ROOT  = Path(__file__).parent.parent / "incoming_data"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    BASE_URL,
}
# ──────────────────────────────────────────────────────────────────────────────


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def get_csrf(session: requests.Session) -> str:
    """CSRF 토큰을 발급받는다."""
    url  = f"{BASE_URL}/portal/main/contents.do?menuNo={CSRF_MENU_NO}"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    meta = soup.select_one("meta[name='_csrf']")
    if not meta:
        raise RuntimeError("CSRF 토큰을 찾을 수 없습니다")
    return meta["content"]


def fetch_page(
    session:      requests.Session,
    csrf:         str,
    committee_cd: str,
    page:         int = 1,
) -> dict:
    # menuNo는 API 필터링에 영향 없음 — committeeCd + 날짜로 필터링
    payload = {
        "committeeCd": committee_cd,
        "title":       "",
        "beginDate":   BEGIN_DATE,
        "endDate":     END_DATE,
        "_csrf":       csrf,
        "pageIndex":   page,
        "rowPerPage":  PAGE_SIZE,
    }
    resp = session.post(LIST_API, data=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_all_rows(session: requests.Session, csrf: str, committee_cd: str) -> list[dict]:
    """페이지네이션을 반복해 전체 회의록 행을 반환한다.
    API는 rowPerPage를 무시하고 페이지당 10건 고정으로 반환한다."""
    rows, page = [], 1
    total_cnt  = None

    while True:
        data  = fetch_page(session, csrf, committee_cd, page)
        batch = data.get("resultList") or []

        if not batch:
            break

        rows.extend(batch)

        if total_cnt is None:
            try:
                total_cnt = int(data.get("resultCnt", 0))
            except (ValueError, TypeError):
                total_cnt = 0

        print(f"  페이지 {page}: {len(batch)}건 / 누적 {len(rows)}건 (전체 {total_cnt}건)")

        if total_cnt and len(rows) >= total_cnt:
            break

        page += 1
        time.sleep(DELAY)

    return rows


def make_filename(row: dict, pdf_url: str) -> str:
    date_raw = row.get("confDate") or "00000000"
    date     = re.sub(r"[^\d]", "", str(date_raw))[:8]
    mtg_no   = re.sub(r"[^\w]", "", str(row.get("conferNum") or "0"))
    qs       = pdf_url.split("?")[-1] if "?" in pdf_url else pdf_url
    id_m     = re.search(r"(\d{4,})", qs)
    pdf_id   = id_m.group(1) if id_m else "0"
    return f"{date}_{mtg_no}_{pdf_id}.pdf"


def download_pdf(session: requests.Session, url: str, dest: Path) -> None:
    """PDF를 다운로드한다. 중복 파일도 덮어쓴다."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = session.get(url, timeout=60, stream=True)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


def crawl_committee(
    session:      requests.Session,
    csrf:         str,
    folder:       str,
    full_name:    str,
    committee_cd: str,
) -> None:
    print(f"\n{'='*60}")
    print(f"[{folder}] {full_name}  (committeeCd={committee_cd})")

    rows = fetch_all_rows(session, csrf, committee_cd)
    print(f"  수집 완료: {len(rows)}건")

    out_dir              = OUTPUT_ROOT / folder
    downloaded = errors  = 0

    for row in rows:
        pdf_url = row.get("pdfLinkUrl", "")
        if not pdf_url:
            continue
        if not pdf_url.startswith("http"):
            pdf_url = BASE_URL + pdf_url

        fname = make_filename(row, pdf_url)
        dest  = out_dir / fname

        try:
            download_pdf(session, pdf_url, dest)
            print(f"  ↓ {fname}")
            downloaded += 1
        except Exception as exc:
            print(f"  [오류] {fname}: {exc}")
            errors += 1

        time.sleep(DELAY)

    print(f"  완료 - 다운로드 {downloaded} / 오류 {errors}")


def main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    targets = set(sys.argv[1:])
    session = make_session()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    print("CSRF 토큰 발급 중...")
    csrf = get_csrf(session)
    print("CSRF 발급 완료\n")

    for folder, (full_name, committee_cd) in COMMITTEES.items():
        if targets and folder not in targets:
            continue
        try:
            crawl_committee(session, csrf, folder, full_name, committee_cd)
        except Exception as exc:
            print(f"\n[치명 오류] {folder}: {exc}")

    print("\n모든 위원회 크롤링 완료")


if __name__ == "__main__":
    main()
