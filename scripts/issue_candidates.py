"""이슈 후보 탐사 (POL-3 단계 1) — 코퍼스에서 이슈 후보 40~60개를 근거와 함께 추출.

네 가지 신호를 교차한다 (docs/issue_module_spec.md):
  1. 시계열 스파이크 — 위원회×월 발언량 급증 (사건형 후보)
  2. agenda 빈발 줄 — 안건 섹션 반복 의제 (정책형 후보)
  3. LLM 표본 요약 — 위원회×분기 표본 청크의 반복 주제
  4. query_logs — 실사용 질문 교차

산출: data/issues/candidates_report.md (사용자 검수용 — 확정은 사람이 한다)
실행: python scripts/issue_candidates.py
"""

import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

if __name__ == "__main__":  # import 시(테스트) 부작용 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "data" / "issues"
REPORT = OUT_DIR / "candidates_report.md"
NORMALIZED_DIR = ROOT / "data" / "v1" / "normalized"

SPIKE_RATIO = 1.8       # 월 발언량 ≥ 위원회 월 중앙값 × 이 배율 → 스파이크
SAMPLE_PER_CELL = 30    # 위원회×분기당 LLM 요약 표본 청크 수
MIN_AGENDA_LEN = 8      # 이보다 짧은 안건 줄은 잡음 ("산회" 등)
_MODEL = "gpt-4o-mini"


def detect_spikes(rows: list[tuple], ratio: float = SPIKE_RATIO) -> list[dict]:
    """(committee, 'YYYY-MM', turn_count) 목록 → 스파이크 목록 (ratio 내림차순).

    중앙값 기준 — 평균은 스파이크 자신에게 끌려간다. 월이 3개 미만인 위원회는
    중앙값이 무의미하므로 판단하지 않는다.
    """
    by_com = defaultdict(list)
    for com, month, cnt in rows:
        by_com[com].append((month, cnt))
    out = []
    for com, months in by_com.items():
        if len(months) < 3:
            continue
        for month, cnt in months:
            # 현재 행을 제외한 다른 행들의 중앙값
            other_counts = [c for m, c in months if m != month]
            med = median(other_counts)
            if med <= 0:
                continue
            if cnt >= ratio * med:
                out.append({"committee": com, "month": month, "count": cnt,
                            "median": med, "ratio": round(cnt / med, 2)})
    out.sort(key=lambda s: s["ratio"], reverse=True)
    return out


def top_agenda_lines(lines: list[str], top_n: int = 40) -> list[tuple[str, int]]:
    """agenda 섹션 줄들 → (정규화 줄, 빈도) 상위 top_n. 짧은 줄(의사진행 잡음)은 제외."""
    counter = Counter(
        " ".join(line.split())
        for line in lines
        if len(" ".join(line.split())) >= MIN_AGENDA_LEN
    )
    return counter.most_common(top_n)


def parse_topics(content: str) -> list[str]:
    """LLM 요약 응답 '{"topics": [...]}' → 주제 목록. 형식 위반은 빈 목록 (후보 누락 무해)."""
    try:
        topics = json.loads(content).get("topics")
    except (json.JSONDecodeError, AttributeError):
        return []
    if not isinstance(topics, list):
        return []
    return [t for t in topics if isinstance(t, str)]


def _monthly_counts() -> list[tuple]:
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT co.name, to_char(c.meeting_date, 'YYYY-MM') AS month,
                   count(DISTINCT c.turn_id)
            FROM chunks c JOIN committees co ON co.committee_id = c.committee_id
            GROUP BY co.name, month
            ORDER BY co.name, month
        """)
        return cur.fetchall()


def _agenda_lines() -> list[str]:
    """normalized.jsonl 의 agenda 세그먼트 줄 수집 (767개 source 전체)."""
    lines = []
    for f in sorted(NORMALIZED_DIR.glob("*/normalized.jsonl")):
        for raw in f.read_text(encoding="utf-8").splitlines():
            page = json.loads(raw)
            for seg in page.get("segments", []):
                if seg.get("section_type") == "agenda":
                    lines.extend(seg["text"].splitlines())
    return lines


def _quarter_cells() -> list[tuple]:
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT co.name, to_char(date_trunc('quarter', c.meeting_date), 'YYYY-"Q"Q') AS q
            FROM chunks c JOIN committees co ON co.committee_id = c.committee_id
            GROUP BY co.name, q ORDER BY co.name, q
        """)
        return cur.fetchall()


def _sample_texts(committee: str, quarter: str, n: int) -> list[str]:
    """위원회×분기 무작위 표본 (is_short 제외, 500자 절단). seed 는 SQL setseed 로 고정."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT setseed(0.42)")
        cur.execute("""
            SELECT left(c.text, 500)
            FROM chunks c JOIN committees co ON co.committee_id = c.committee_id
            WHERE co.name = %s
              AND to_char(date_trunc('quarter', c.meeting_date), 'YYYY-"Q"Q') = %s
              AND NOT c.is_short
            ORDER BY random() LIMIT %s
        """, (committee, quarter, n))
        return [r[0] for r in cur.fetchall()]


_SUMMARY_SYSTEM = """당신은 국회 회의록 발언 표본에서 반복되는 쟁점을 뽑는 도우미다.
발언 표본이 주어지면, 반복적으로 다뤄지는 사건·정책 주제 3~5개를 짧은 명사구로 뽑아라.
의사진행(개의·산회·표결)·인사말은 주제가 아니다.
반드시 아래 JSON 만 출력: {"topics": ["주제1", "주제2", ...]}"""


def _summarize_cell(client, committee: str, quarter: str, texts: list[str]) -> list[str]:
    docs = "\n---\n".join(texts)
    resp = client.chat.completions.create(
        model=_MODEL, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": _SUMMARY_SYSTEM},
                  {"role": "user", "content": f"[{committee} {quarter} 표본]\n{docs}"}],
    )
    return parse_topics(resp.choices[0].message.content)


def _user_questions() -> list[str]:
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT question FROM query_logs ORDER BY question")
        return [r[0] for r in cur.fetchall()]


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool
    from search_vector import _get_client

    init_pool()
    print("[1/4] 시계열 스파이크...")
    spikes = detect_spikes(_monthly_counts())
    print(f"      {len(spikes)}건")

    print("[2/4] agenda 빈발 줄...")
    agenda_top = top_agenda_lines(_agenda_lines())

    print("[3/4] LLM 표본 요약 (위원회×분기)...")
    client = _get_client()
    topic_counter = Counter()
    cells = _quarter_cells()
    for i, (com, q) in enumerate(cells, 1):
        texts = _sample_texts(com, q, SAMPLE_PER_CELL)
        if not texts:
            continue
        for t in _summarize_cell(client, com, q, texts):
            topic_counter[(com, t)] += 1
        print(f"      {i}/{len(cells)} {com} {q}")

    print("[4/4] query_logs 교차...")
    questions = _user_questions()
    close_pool()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# 이슈 후보 리포트 (POL-3 단계 1 — 사용자 검수용)", "",
             "> 아래 신호를 보고 20~30개를 확정해 issues_seed.json 을 만든다.",
             "", "## 1. 시계열 스파이크 (사건형 후보)", "",
             "| 위원회 | 월 | 발언(turn) | 위원회 월 중앙값 | 배율 |", "|---|---|---|---|---|"]
    lines += [f"| {s['committee']} | {s['month']} | {s['count']} | {s['median']} | {s['ratio']} |"
              for s in spikes]
    lines += ["", "## 2. agenda 빈발 의제 (정책형 후보)", ""]
    lines += [f"- ({n}회) {t}" for t, n in agenda_top]
    lines += ["", "## 3. LLM 표본 요약 반복 주제 (위원회, 등장 분기 수)", ""]
    lines += [f"- {com}: {t} ({n}분기)"
              for (com, t), n in topic_counter.most_common(80)]
    lines += ["", "## 4. 실사용 질문 (query_logs)", ""]
    lines += [f"- {q}" for q in questions]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {REPORT}")


if __name__ == "__main__":
    main()
