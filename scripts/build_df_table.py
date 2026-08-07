"""
토큰 문서빈도(DF) 표 생성 — 키워드 검색의 IDF 가중치용.

왜 필요한가 (2026-08-07 실측)
    키워드 점수가 "토큰 하나 맞으면 +1" 이라 모든 단어가 같은 값이었다. 그런데
    코퍼스에서 "정부"는 30,696건(7.30%), "티메프"는 99건(0.02%) — 310배 차이다.
    그 결과 "티메프 사태의 정부 책임 범위를 두고 여당과 야당의 시각은…" 질문에서
    '사태·정부·책임·여당·야당' 5개를 가진 **국방위 안보 발언이 1위**가 되고,
    '티메프' 하나만 가진 진짜 근거를 눌렀다. 상위 30건 중 티메프가 실제로 든 것은
    6건뿐이었다.
    비교 질문(여당·야당·시각·범위 등 흔한 말이 많다)이 모델 3종에서 모두 0/3 이던
    원인이 여기였다 — 생성이 아니라 **검색이 엉뚱한 근거를 준 것**이다.

왜 미리 계산하는가
    질의 때마다 세면 흔한 토큰 하나에 ~400ms 가 든다(8토큰이면 2초 이상).
    게다가 배포 코퍼스에는 trgm 인덱스가 없어(런북이 용량 때문에 의도적으로 생략)
    더 느리다. 표를 파일로 두면 질의 때 비용이 0 이다.

어휘 선정
    코퍼스를 표본추출해 실제로 쓰이는 토큰을 뽑고, 그중 상위 빈도만 담는다.
    표에 없는 토큰은 **드문 것으로 간주**한다 — 드문 쪽이 기본값이어야 안전하다.
    (흔한데 빠진 토큰이 과대평가되는 것보다, 드문 토큰이 제 값을 받는 게 중요하다.
     그리고 실제로 흔한 토큰일수록 표본에 잡힐 확률이 높다.)

실행: python scripts/build_df_table.py
출력: data/token_df.json  {"total": N, "df": {"토큰": 건수, ...}}
"""

import io
import json
import sys
import time
from collections import Counter
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backend"))

OUT = ROOT / "data" / "token_df.json"
SAMPLE = 30000     # 표본 청크 수 — 어휘 수집용
TOP_N = 1200       # DF 를 실제로 셀 상위 빈도 토큰 수
MIN_SAMPLE_HITS = 8   # 표본에서 이보다 드물면 어차피 드문 토큰 — 세지 않는다


def main() -> int:
    from db import close_pool, get_conn, init_pool
    from query_parser import content_tokens

    init_pool()
    t0 = time.time()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks")
        total = cur.fetchone()[0]
        # 균일 표본 — 특정 위원회·시기에 쏠리지 않게 chunk_id 해시로 흩는다
        cur.execute("""
            SELECT text FROM chunks
            WHERE length(text) >= 60
            ORDER BY md5(chunk_id)
            LIMIT %s
        """, (SAMPLE,))
        texts = [r[0] for r in cur.fetchall()]
    print(f"표본 {len(texts)}건 / 전체 {total}건 ({time.time() - t0:.0f}초)")

    freq: Counter = Counter()
    for t in texts:
        freq.update(set(content_tokens(t)))     # 문서당 1회만 — 문서빈도이므로
    cands = [w for w, n in freq.most_common(TOP_N) if n >= MIN_SAMPLE_HITS]
    print(f"어휘 후보 {len(cands)}개 (표본 {MIN_SAMPLE_HITS}건 이상)")

    df: dict[str, int] = {}
    t1 = time.time()
    with get_conn() as conn, conn.cursor() as cur:
        for i, w in enumerate(cands, 1):
            cur.execute("SELECT count(*) FROM chunks WHERE text ILIKE %s", (f"%{w}%",))
            df[w] = cur.fetchone()[0]
            if i % 100 == 0:
                print(f"  {i}/{len(cands)} ... {time.time() - t1:.0f}초")
    close_pool()

    OUT.write_text(json.dumps({"total": total, "df": df}, ensure_ascii=False,
                              sort_keys=True, indent=0), encoding="utf-8")
    top = sorted(df.items(), key=lambda kv: -kv[1])[:12]
    print(f"\n저장: {OUT}  ({len(df)}토큰, {time.time() - t0:.0f}초)")
    print("가장 흔한 토큰: " + ", ".join(f"{w}({n / total:.1%})" for w, n in top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
