"""report 브리핑 이슈 분석 주입 (POL-8).

질문에서 이슈를 감지(시드 키워드 최다 매칭)하고 구도(POL-6)·타임라인(POL-4)·
주요 행위자(POL-5)를 컴팩트 텍스트 블록으로 조립해 answer.py 가 user 메시지에
별도 경계로 삽입한다. 감지는 보수적 — 동률·무매칭이면 주입 생략(오탐 없음 우선).

스펙: docs/superpowers/specs/2026-07-11-pol8-report-issue-context-design.md
"""

_STANCE_KO = {"support": "찬성", "oppose": "반대", "concern": "우려",
              "mixed": "혼재", "no_stance": "무입장"}

_GUIDE = (
    "(코퍼스 분석 기준 — 아래 수치는 회의록 자동 분석 결과다. 개요·쟁점별 정리에 "
    '활용하되 "코퍼스 분석 기준"으로 표기하고, 발언 인용 근거는 [n] 본문만 쓴다. '
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


def build_issue_block(party_data: dict, timeline: dict | None, actors: list[dict]) -> str:
    """구도·피크·행위자 → LLM 주입용 컴팩트 텍스트. 순수 함수."""
    lines = [f"[이슈: {party_data['title']}]", _GUIDE]
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
