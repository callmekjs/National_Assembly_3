"""
[5] policy_enricher_v1
발언 turn에 정책 도메인 메타데이터를 추가한다. (rule-based v1)

추가 필드:
  policy_domain   : 위원회 → 정책 분야 매핑
  bill_refs       : 발언 내 법안명 추출
  utterance_type  : question / statement / motion
  stance_signals  : positive / negative / neutral
  mentions        : 언급된 부처·기관명

입력 : data/v1/parsed/{source_id}/turns.jsonl
출력 : data/v1/enriched/{source_id}/enriched_turns.jsonl

실행:
    python scripts/policy_enricher_v1.py              # 전체
    python scripts/policy_enricher_v1.py 과방위 외통위  # 특정 위원회만
"""

import io
import json
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ENRICHER_VERSION = "v1.0"

INPUT_ROOT  = Path(__file__).parent.parent / "data" / "v1" / "parsed"
OUTPUT_ROOT = Path(__file__).parent.parent / "data" / "v1" / "enriched"

# ── 위원회 → 정책 분야 ─────────────────────────────────────────────────────────
_COMMITTEE_DOMAIN = {
    "과학기술정보방송통신위원회": "과학기술/방송통신/ICT",
    "외교통일위원회":           "외교/통일/안보",
    "정무위원회":               "정무/공정거래/금융감독",
    "재정경제기획위원회":        "기획재정/세제/예산",
    "행정안전위원회":           "행정안전/지방자치",
    "보건복지위원회":           "보건/복지/의료",
    "국토교통위원회":           "국토/교통/주택",
    "산업통상자원중소벤처기업위원회": "산업통상/에너지/중소기업",
    "국방위원회":               "국방/방위산업",
}

# ── 법안명 패턴 ────────────────────────────────────────────────────────────────
_BILL_PATTERNS = [
    re.compile(r"[가-힣A-Za-z0-9]+법\s*(?:개정안|제정안|일부개정안|전부개정안)"),
    re.compile(r"[가-힣A-Za-z0-9]+\s*(?:기본법|특별법|특례법|촉진법|지원법|보호법)"),
    re.compile(r"제\d+조(?:의\d+)?"),   # 조문 인용
]

# ── 발언 유형 판별 ────────────────────────────────────────────────────────────
_QUESTION_RE = re.compile(
    r"[?？]|질의|질문|여쭤|물어|어떻게\s*생각|않습니까\??|않나요|인가요\??|하십니까\??"
)
_MOTION_KW = frozenset(["찬성", "반대", "의결", "동의합니다", "가결", "부결", "표결"])

# ── 입장 신호 ──────────────────────────────────────────────────────────────────
_POSITIVE_KW = frozenset(["찬성", "동의", "지지", "환영", "긍정적", "바람직", "적극"])
_NEGATIVE_KW = frozenset(["반대", "우려", "문제", "비판", "부정적", "심각", "부적절", "잘못"])

# ── 부처·기관명 패턴 ───────────────────────────────────────────────────────────
_MINISTRY_RE = re.compile(
    r"과학기술정보통신부|방송통신위원회|방통위|교육부|국방부|외교부|통일부|"
    r"행정안전부|행안부|기획재정부|기재부|보건복지부|복지부|국토교통부|국토부|"
    r"산업통상자원부|산업부|중소벤처기업부|중기부|환경부|고용노동부|고용부|"
    r"농림축산식품부|농식품부|해양수산부|해수부|법무부|여성가족부|여가부|"
    r"문화체육관광부|문체부|과기부|방위사업청|경찰청|소방청|질병관리청|식품의약품안전처|"
    r"공정거래위원회|공정위|금융위원회|금융위|금융감독원|금감원|"
    r"국세청|관세청|조달청|통계청|병무청"
)


def _extract_bill_refs(text: str) -> list[str]:
    refs = []
    for pat in _BILL_PATTERNS:
        refs.extend(pat.findall(text))
    seen = set()
    result = []
    for r in refs:
        r = r.strip()
        if r and r not in seen:
            seen.add(r)
            result.append(r)
    return result


def _utterance_type(text: str) -> str:
    words = set(text.split())
    if words & _MOTION_KW:
        return "motion"
    if _QUESTION_RE.search(text):
        return "question"
    return "statement"


def _stance_signals(text: str) -> str:
    words = set(text.split())
    pos = bool(words & _POSITIVE_KW)
    neg = bool(words & _NEGATIVE_KW)
    if pos and neg:
        return "mixed"
    if pos:
        return "positive"
    if neg:
        return "negative"
    return "neutral"


def _mentions(text: str) -> list[str]:
    found = _MINISTRY_RE.findall(text)
    seen = set()
    result = []
    for m in found:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


def enrich_turn(turn: dict) -> dict:
    text      = turn.get("text", "")
    committee = turn.get("committee", "")
    enriched  = dict(turn)
    enriched["policy_domain"]    = _COMMITTEE_DOMAIN.get(committee, "기타")
    enriched["bill_refs"]        = _extract_bill_refs(text)
    enriched["utterance_type"]   = _utterance_type(text)
    enriched["stance_signals"]   = _stance_signals(text)
    enriched["mentions"]         = _mentions(text)
    enriched["enricher_version"] = ENRICHER_VERSION
    return enriched


def enrich_source(source_id: str) -> tuple[int, str | None]:
    in_path  = INPUT_ROOT  / source_id / "turns.jsonl"
    out_dir  = OUTPUT_ROOT / source_id
    out_path = out_dir / "enriched_turns.jsonl"

    if not in_path.exists():
        return 0, f"turns.jsonl 없음: {in_path}"
    if out_path.exists():
        return 0, None  # 이미 처리됨

    turns = []
    with open(in_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                turns.append(json.loads(line))

    enriched = [enrich_turn(t) for t in turns]

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for t in enriched:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    return len(enriched), None


def main() -> None:
    targets = set(sys.argv[1:])

    if not INPUT_ROOT.exists():
        print(f"parsed 데이터 없음: {INPUT_ROOT}")
        sys.exit(1)

    source_ids = sorted(p.name for p in INPUT_ROOT.iterdir() if p.is_dir())
    if targets:
        source_ids = [s for s in source_ids if any(s.startswith(t) for t in targets)]

    total = errors = 0
    for sid in source_ids:
        n, err = enrich_source(sid)
        if err:
            print(f"  [오류] {sid}: {err}")
            errors += 1
        else:
            print(f"  ✓ {sid}  ({n}턴 enriched)")
            total += n

    print(f"\nenrich 완료 — 총 {total}턴 / 오류 {errors}개")
    print(f"출력 위치: {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
