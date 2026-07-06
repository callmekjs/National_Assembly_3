"""
[검증] etl_audit
DB의 청크를 무작위 샘플링해 원본 추출 텍스트(extract 레이어)와 대조한다.

검증 항목 (청크당):
    1. 본문 일치  — 청크 text가 원본 page_start~page_end 페이지 텍스트 안에 존재하는가
    2. 발언자 일치 — speaker 이름이 해당 페이지에 실제로 등장하는가
    3. 페이지 정확 — 위 두 검사가 청크가 주장하는 페이지 범위 안에서 성립하는가

비교 방식: 공백·줄바꿈을 제거한 문자열로 부분 일치 검사
(normalizer가 줄바꿈을 재구성하므로 공백 무시 비교가 필요)

실행:
    python scripts/etl_audit.py            # 30개 샘플
    python scripts/etl_audit.py 100        # 100개 샘플
"""

import io
import json
import os
import random
import re
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent
EXTRACT_ROOT = PROJECT_ROOT / "data" / "v1" / "extract"

_WS = re.compile(r"\s+")


def squash(s: str) -> str:
    """모든 공백 제거 — 줄바꿈 재구성 차이를 무시하고 내용만 비교."""
    return _WS.sub("", s or "")


def load_pages(source_id: str, p_start: int, p_end: int, margin: int = 1) -> dict[int, str]:
    """extract 레이어에서 해당 페이지 범위(±margin)의 원본 텍스트를 페이지별로 반환."""
    path = EXTRACT_ROOT / source_id / "pages.jsonl"
    if not path.exists():
        return {}
    pages: dict[int, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            page = json.loads(line)
            num = page.get("page", -1)
            if p_start - margin <= num <= p_end + margin:
                pages[num] = page.get("text", "")
    return pages


def _boundary_split_ok(text_sq: str, pages_sq: dict[int, str]) -> bool:
    """
    페이지 경계 걸침 검사: 텍스트의 최장 접두사가 어떤 페이지에 있고,
    나머지 전체가 그 이후 페이지에 있으면 통과.
    (원본에는 페이지 사이 헤더가 껴 있어 연속 문자열 검사가 실패하는 경우 대응)
    """
    for start_pg in sorted(pages_sq):
        page_txt = pages_sq[start_pg]
        # 이 페이지에 있는 최장 접두사 길이 (이진 탐색)
        lo, hi = 0, len(text_sq)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if text_sq[:mid] in page_txt:
                lo = mid
            else:
                hi = mid - 1
        if lo < 10:          # 접두사가 이 페이지에 없다시피 함
            continue
        rest = text_sq[lo:]
        if not rest:         # 전체가 한 페이지에 있음
            return True
        # 나머지가 이후 페이지 어딘가에 통째로 존재하는가 (재귀적으로 다중 경계도 허용)
        later = {p: t for p, t in pages_sq.items() if p > start_pg}
        if any(rest in t for t in later.values()):
            return True
        if later and _boundary_split_ok(rest, later):
            return True
    return False


def audit_chunk(chunk: dict) -> dict:
    """
    청크 1개를 원본과 대조. 결과 dict 반환.

    본문 검사: 20자 조각(probe) 여러 개를 텍스트 곳곳에서 뽑아 원본 존재 여부 확인.
    페이지 경계를 걸치는 조각은 원본에 페이지 헤더가 껴 있어 실패할 수 있으므로
    (normalizer가 헤더를 제거하고 발언을 이어붙임 — 정상 동작),
    조각 중 1개까지의 불일치는 허용한다.
    """
    pages = load_pages(chunk["source_id"], chunk["page_start"], chunk["page_end"])
    pages_sq = {p: squash(t) for p, t in pages.items()}
    raw_sq = "".join(pages_sq.values())
    text_sq = squash(chunk["text"])

    # 텍스트 전체에 고르게 분포한 20자 probe 최대 5개
    probes = []
    n = len(text_sq)
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        start = min(int(n * frac), max(0, n - 20))
        p = text_sq[start:start + 20]
        if len(p) >= 10 and p not in probes:
            probes.append(p)

    hits = sum(1 for p in probes if p in raw_sq)
    # 경계 걸침 1개 허용 (probe 2개 이하인 짧은 텍스트는 전부 일치 요구)
    text_ok = hits >= (len(probes) - 1 if len(probes) >= 3 else len(probes))

    # probe 실패 시 페이지 경계 분할 검사로 재확인
    if not text_ok:
        text_ok = _boundary_split_ok(text_sq, pages_sq)

    speaker_ok = squash(chunk["speaker"]) in raw_sq if chunk["speaker"] else False

    return {
        "chunk_id": chunk["chunk_id"],
        "speaker": chunk["speaker"],
        "pages": f"{chunk['page_start']}-{chunk['page_end']}",
        "probe_hits": f"{hits}/{len(probes)}",
        "text_ok": text_ok,
        "speaker_ok": speaker_ok,
        "all_ok": text_ok and speaker_ok,
    }


def main() -> None:
    n_samples = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    random.seed()  # 매번 다른 샘플

    load_dotenv(PROJECT_ROOT / ".env")
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()

    # 무작위 샘플 (TABLESAMPLE 은 근사라 ORDER BY random() 사용 — 감사 용도로 충분)
    cur.execute(
        """
        SELECT chunk_id, source_id, speaker, page_start, page_end, text
        FROM chunks
        WHERE text IS NOT NULL AND length(text) >= 20
        ORDER BY random()
        LIMIT %s
        """,
        (n_samples,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    results = []
    for r in rows:
        chunk = dict(zip(["chunk_id", "source_id", "speaker", "page_start", "page_end", "text"], r))
        results.append(audit_chunk(chunk))

    passed = [r for r in results if r["all_ok"]]
    failed = [r for r in results if not r["all_ok"]]

    print(f"=== ETL 원본 대조 감사 — 무작위 {len(results)}개 청크 ===\n")
    print(f"  본문   일치 : {sum(r['text_ok'] for r in results)}/{len(results)}")
    print(f"  발언자 일치 : {sum(r['speaker_ok'] for r in results)}/{len(results)}")
    print(f"  전체 통과   : {len(passed)}/{len(results)}  ({len(passed)/len(results)*100:.0f}%)")

    if failed:
        print(f"\n  불일치 {len(failed)}건:")
        for r in failed:
            flags = []
            if not r["text_ok"]:    flags.append(f"본문(probe {r['probe_hits']})")
            if not r["speaker_ok"]: flags.append("발언자")
            print(f"    - {r['chunk_id']}  (p.{r['pages']}, {r['speaker']})  실패: {','.join(flags)}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
