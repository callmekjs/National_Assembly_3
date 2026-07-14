"""report 브리핑 이슈 분석 주입 (POL-8).

질문에서 이슈를 감지(시드 키워드 최다 매칭)하고 구도(POL-6)·타임라인(POL-4)·
주요 행위자(POL-5)를 컴팩트 텍스트 블록으로 조립해 answer.py 가 user 메시지에
별도 경계로 삽입한다. 감지는 보수적 — 동률·무매칭이면 주입 생략(오탐 없음 우선).

스펙: docs/superpowers/specs/2026-07-11-pol8-report-issue-context-design.md
"""

_STANCE_KO = {"support": "찬성", "oppose": "반대", "concern": "우려",
              "mixed": "혼재", "no_stance": "무입장"}

_GUIDE = (
    "(코퍼스 분석 기준 — 아래 수치는 회의록 자동 분석 결과다. 지시: '## 개요' 섹션에 "
    "이 데이터를 근거로 정당별 구도(인원·방향)와 발언 피크 시기를 \"(코퍼스 분석 기준)\" "
    "표기와 함께 2~3문장으로 반드시 요약해 포함한다. 개별 발언 인용 근거는 [n] 본문만 "
    "쓴다. 입장 세분류(찬성/우려 경계)는 오차가 있으니 방향(찬반) 중심으로 서술한다.)"
)
# qa 비교 질문용 — 섹션 구조가 없으므로 '개요 섹션' 지시 대신 서술 방식만 지시.
# 소수 근거 발언의 진영 일반화(2026-07-14 프로브 실측)를 전체 판정 집계로 대체.
_GUIDE_QA = (
    "(코퍼스 분석 기준 — 아래 수치는 회의록 자동 분석 결과다. 지시: 여야·정당의 "
    "전체 구도는 개별 발언이 아니라 이 데이터(정당별 인원·방향)를 근거로 서술하고 "
    "\"(코퍼스 분석 기준)\"을 병기한다. 개별 발언 인용은 [n] 본문만 쓴다. "
    "입장 세분류(찬성/우려 경계)는 오차가 있으니 방향(찬반) 중심으로 서술한다.)"
)
_LOW_WARN = "⚠ 이 이슈의 자동 매핑 정밀도는 기준 미달 — 수치 해석 주의"

_issue_index: list[dict] | None = None


def detect_issue(question: str, index: list[dict]) -> dict | None:
    """시드 키워드 부분일치 최다 이슈. 0개 또는 최다 동률이면 None (보수적)."""
    best, best_n, tie = None, 0, False
    for it in index:
        n = sum(1 for k in it.get("seed_keywords", []) if k and k in question)
        if n > best_n:
            best, best_n, tie = it, n, False
        elif n == best_n and n > 0:
            tie = True
    return None if tie else best


def _dist(d: dict) -> str:
    return f"찬{d['support']}·반{d['oppose']}·우{d['concern']}·혼{d['mixed']}·무{d['no_stance']}"


def _badge(side: list | None) -> str:
    if not side:
        return ""
    return f" [{side[0]}]" if len(set(side)) == 1 else f" [{side[0]}→{side[1]}]"


def build_issue_block(party_data: dict, timeline: dict | None, actors: list[dict],
                      guide: str = _GUIDE) -> str:
    """구도·피크·행위자 → LLM 주입용 컴팩트 텍스트. 순수 함수."""
    lines = [f"[이슈: {party_data['title']}]", guide]
    if party_data.get("mapping_quality") == "low":
        lines.append(_LOW_WARN)

    parts = [f"{r['party']} {r['actor_count']}명({_dist(r['stance_dist'])})"
             f"{_badge(r.get('side_by_period'))}" for r in party_data["parties"]]
    lines.append("- 구도: " + " / ".join(parts))

    months = (timeline or {}).get("months", [])
    peaks = sorted((m for m in months if m["mapped_core_turns"] > 0),
                   key=lambda m: -m["mapped_core_turns"])[:3]
    key = "mapped_core_turns"
    if not peaks:
        peaks = sorted((m for m in months if m["corpus_turns"] > 0),
                       key=lambda m: -m["corpus_turns"])[:3]
        key = "corpus_turns"
    if peaks:
        lines.append("- 발언 피크: " + ", ".join(f"{m['month']}({m[key]}턴)" for m in peaks))

    lookup = {a["speaker"]: (r["party"], a["stance"])
              for r in party_data["parties"] for a in r["actors"]}
    named = []
    for a in actors:
        if a["speaker"] in lookup and len(named) < 5:
            party, stance = lookup[a["speaker"]]
            gov = "정부측, " if party == "정부측" else ""
            named.append(f"{a['speaker']}({gov}{a['n_turns']}턴, {_STANCE_KO[stance]})")
    if named:
        lines.append("- 주요 행위자: " + ", ".join(named))
    return "\n".join(lines)


def load_issue_index() -> list[dict]:
    """issues 테이블 1회 조회 후 모듈 캐시 (party._load_map 패턴). 24행 수준.

    주의: 프로세스 수명 캐시 — 이슈를 새로 추가하면 백엔드 재시작 전까지 감지 안 됨.
    """
    global _issue_index
    if _issue_index is None:
        from db import get_conn
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT issue_id, title, seed FROM issues")
            _issue_index = [
                {"issue_id": i, "title": t,
                 "seed_keywords": (s or {}).get("seed_keywords", [])}
                for i, t, s in cur.fetchall()
            ]
    return _issue_index


def top_actors(issue_id: str, limit: int = 8) -> list[dict]:
    """이슈 내 발언 수 상위 행위자. 구도 제외자(증인 등) 탈락 대비 5명보다 여유 조회."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT speaker, count(*) AS n_turns FROM issue_stances
            WHERE issue_id = %s GROUP BY speaker
            ORDER BY n_turns DESC, speaker LIMIT %s
        """, (issue_id, limit))
        return [{"speaker": s, "n_turns": n} for s, n in cur.fetchall()]


def issue_context_for(question: str, style: str = "report") -> tuple[str, dict] | None:
    """질문 → (분석 블록, issue_context dict) 또는 None (감지 실패·판정 없는 이슈).

    style: "report"(개요 섹션 요약 지시) | "qa"(비교 서술 방식만 지시 — 섹션 구조 없음)."""
    hit = detect_issue(question, load_issue_index())
    if hit is None:
        return None
    from issues import issue_party_stances, issue_timeline
    party_data = issue_party_stances(hit["issue_id"])
    if party_data is None:
        return None
    block = build_issue_block(party_data, issue_timeline(hit["issue_id"]),
                              top_actors(hit["issue_id"]),
                              guide=_GUIDE_QA if style == "qa" else _GUIDE)
    return block, {"issue_id": hit["issue_id"], "title": hit["title"]}
