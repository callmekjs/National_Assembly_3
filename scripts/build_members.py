"""
22대 의원-정당 매핑 구축 (정당 모듈 — 3단계 행위자 분석 1차).

흐름: 열린국회정보 ALLNAMEMBER 수집 → 22대 필터 → 위성정당 정규화
     → 원본 JSON 보존 → 한자 별칭 파일 생성 → DB members 적재

실측 주의 (2026-07-03, docs/party_module_spec.md):
  - 브라우저 User-Agent 필수 (파이썬 기본 UA는 게이트웨이가 HTTP 400)
  - 대수 필터는 서버에서 안 먹힘 → 전체 수집 후 GTELT_ERACO 로 클라이언트 필터
  - PLPT_NM 은 커리어 전체 이력 → 마지막 항목 = 최종 당적
  - 위성정당(국민의미래·더불어민주연합)이 최종 당적으로 남은 의원 34명 → 모정당 정규화

실행: python scripts/build_members.py
"""

import io
import json
import os
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from psycopg2.extras import execute_values

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from db import init_pool, close_pool, get_conn  # noqa: E402

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "data" / "members"
MEMBERS_JSON = OUT_DIR / "members_22.json"
HANJA_ALIASES_JSON = OUT_DIR / "hanja_aliases.json"

API_URL = "https://open.assembly.go.kr/portal/openapi/ALLNAMEMBER"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}

def normalize_party(plpt_nm: str) -> tuple[str, str]:
    """PLPT_NM(커리어 이력) → (최종 당적, 원본).

    위성정당(국민의미래·더불어민주연합)도 표기 그대로 둔다 (2026-07-03 사용자 결정 —
    모정당 치환 기각). 여야 판정 시의 모정당 매핑은 backend/party.py 가 담당한다.
    """
    raw = (plpt_nm or "").strip()
    last = raw.split("/")[-1].strip() if raw else ""
    return last, raw


def fetch_all_members(api_key: str) -> list[dict]:
    rows = []
    for page in range(1, 10):
        url = f"{API_URL}?KEY={api_key}&Type=json&pIndex={page}&pSize=1000"
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.load(r)
        body = d["ALLNAMEMBER"]
        total = body[0]["head"][0]["list_total_count"]
        rows += body[1]["row"] if len(body) > 1 else []
        if len(rows) >= total:
            break
    return rows


def main():
    api_key = os.environ.get("OPEN_ASSEMBLY")
    if not api_key:
        print("[FAIL] .env 에 OPEN_ASSEMBLY 키가 없습니다.")
        sys.exit(1)

    print("[1/4] API 수집 중...")
    rows = fetch_all_members(api_key)
    m22 = [r for r in rows if "제22대" in (r.get("GTELT_ERACO") or "")]
    print(f"      전체 {len(rows)}명 → 22대 {len(m22)}명")

    members = []
    for r in m22:
        party, party_raw = normalize_party(r.get("PLPT_NM"))
        members.append({
            "member_id": r["NAAS_CD"],
            "name": (r.get("NAAS_NM") or "").strip(),
            "hanja_name": (r.get("NAAS_CH_NM") or "").strip() or None,
            "party": party,
            "party_raw": party_raw,
            "era": r.get("GTELT_ERACO"),
            "committees": r.get("BLNG_CMIT_NM"),
        })

    satellites = [m for m in members if m["party"] in ("국민의미래", "더불어민주연합")]
    print(f"[2/4] 위성정당 표기 유지 — {len(satellites)}명 (여야 판정은 party.py 가 모정당 기준)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MEMBERS_JSON.write_text(json.dumps(members, ensure_ascii=False, indent=1), encoding="utf-8")

    # 한자 별칭: 한글명↔한자명 쌍 (aliases.py 가 로드해 검색 별칭으로 등록)
    hanja_pairs = [
        {"name": m["name"], "hanja": m["hanja_name"]}
        for m in members
        if m["hanja_name"] and m["hanja_name"] != m["name"]
    ]
    HANJA_ALIASES_JSON.write_text(json.dumps(hanja_pairs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[3/4] 저장: {MEMBERS_JSON.name} ({len(members)}명), {HANJA_ALIASES_JSON.name} ({len(hanja_pairs)}쌍)")

    # DB 적재 (DELETE + 재삽입 — jsonl_to_postgres 패턴)
    init_pool()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS members (
              member_id  TEXT PRIMARY KEY,
              name       TEXT NOT NULL,
              hanja_name TEXT,
              party      TEXT NOT NULL,
              party_raw  TEXT,
              era        TEXT,
              committees TEXT,
              created_at TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_members_name ON members(name);
        """)
        cur.execute("DELETE FROM members")
        execute_values(
            cur,
            """INSERT INTO members (member_id, name, hanja_name, party, party_raw, era, committees)
               VALUES %s""",
            [(m["member_id"], m["name"], m["hanja_name"], m["party"],
              m["party_raw"], m["era"], m["committees"]) for m in members],
        )
        cur.execute("SELECT count(*) FROM members")
        n = cur.fetchone()[0]
    close_pool()

    if n != len(members):
        print(f"[FAIL] 행수 불일치: JSON {len(members)} vs DB {n}")
        sys.exit(1)
    print(f"[4/4] DB members 적재 완료: {n}행 — PASS")


if __name__ == "__main__":
    main()
