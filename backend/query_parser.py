"""
질문 전처리 (RAG-2/4 개선, 2026-07-02).

배경 (eval 실측):
  - date_based 문항 Recall@5 = 0.00 — 질문 속 날짜("2025년 7월 14일")는 본문이 아니라
    메타데이터라서 본문 검색으로는 절대 못 찾는다 → 필터로 변환해야 함
  - 긴 자연어 질문에서 키워드 축 열화 — "정부의", "반응은" 같은 조사 붙은 일반어 토큰이
    점수를 오염 → 조사 제거 + 불용어 필터 필요

기능:
  extract_filters(q) → (cleaned_q, committee, date_from, date_to)
      질문에서 날짜·위원회를 추출해 필터로 변환, 날짜 표현은 질문에서 제거
  content_tokens(q) → [토큰]
      조사 제거 + 불용어 제거를 거친 내용어 토큰
"""

import datetime
import re

# ── 질문 유형 라우터 (2026-07-14) ────────────────────────────────────────────
# 배경 (LLM 답변 프로브 실측): ①특정 주체 질문에 타 발언자 패딩 ②소수 발언을
# 정당 전체 입장으로 과대일반화 ③"경과" 질문에 시간 구조 부재.
# 유형을 감지해 질문 옆에 유형별 지시문을 붙인다 (answer.build_user_message —
# 정당 가드와 같은 '맞불' 배치, 시스템 프롬프트보다 준수율 높음이 기실측).
_COMPARE_RE = re.compile(r"여야|입장\s*차이|정당\s*간|찬반|비교")
_TIMELINE_RE = re.compile(r"경과|추이|흐름|연혁|변해\s*왔|바뀌어\s*왔|어떻게\s*진행")
_ACTOR_RE = re.compile(
    r"(정부|대통령실|[가-힣]{2,4}\s*(?:장관|차관|처장|청장|위원장|의원|후보자))"
    r"[은는이가의]?\s*[^,.\n]{0,10}?(입장|생각|견해|반응|태도|주장|답변)"
)


def classify_question(q: str) -> set:
    """질문 유형 집합 — {'compare','timeline','actor'} 의 부분집합 (복수 가능, 없으면 빈 집합)."""
    types = set()
    if _COMPARE_RE.search(q):
        types.add("compare")
    if _TIMELINE_RE.search(q):
        types.add("timeline")
    if _ACTOR_RE.search(q):
        types.add("actor")
    return types

# ── 위원회 인식 (정식명·통용 표기 → 약칭) ───────────────────────────────────
# 통용 별칭 추가 (2026-07-03): "외교위" 미인식 실측 — 필터를 못 잡으면 전체 검색으로
# 넘어가지만, 복수 위원회 질문에서 등록 표기만 잡혀 나머지가 배제되는 문제와 결합해
# "데이터에 있는데 확인 불가로 답하는" 거짓 부정을 만들었다.
COMMITTEE_MAP = {
    "과학기술정보방송통신위원회": "과방위", "과방위원회": "과방위", "과방위": "과방위",
    "행정안전위원회": "행안위", "행안위원회": "행안위", "행안위": "행안위",
    "국토교통위원회": "국토위", "국토위원회": "국토위", "국토교통위": "국토위", "국토위": "국토위",
    "정무위원회": "정무위", "정무위": "정무위",
    "보건복지위원회": "복지위", "복지위원회": "복지위", "보건복지위": "복지위", "복지위": "복지위",
    "산업통상자원중소벤처기업위원회": "산자중기위", "산자중기위": "산자중기위",
    "산자위": "산자중기위", "산업위": "산자중기위",
    "국방위원회": "국방위", "국방위": "국방위",
    "외교통일위원회": "외통위", "외교통일위": "외통위", "외통위": "외통위",
    "외교위원회": "외통위", "외교위": "외통위", "통일위": "외통위",
    "재정경제기획위원회": "기재위", "기획재정위원회": "기재위", "기획재정위": "기재위",
    "재경위": "기재위", "기재위": "기재위",
}
_COMMITTEE_RE = re.compile(
    "(" + "|".join(sorted(COMMITTEE_MAP, key=len, reverse=True)) + ")"
)

# ── 날짜 인식: "2025년 7월 14일" / "2025년 7월" / "2025-07-14" ────────────────
# 연도 없는 형태("9월 1일", "9월")는 범위 질문("7월 14일부터 9월 1일까지")의
# 뒷날짜로만 쓰인다 — 연도 있는 날짜가 함께 있을 때 그 연도를 물려받는다.
_DATE_FULL_RE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_DATE_MONTH_RE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월(?!\s*\d)")
_DATE_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DATE_MD_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_DATE_M_RE = re.compile(r"(\d{1,2})\s*월(?!\s*\d)")

# ── 조사 (긴 것부터 매칭) ────────────────────────────────────────────────────
_JOSA = sorted(
    ["에서의", "으로써", "으로서", "에서", "에게", "께서", "부터", "까지", "처럼",
     "보다", "하고", "이나", "이란", "라는", "으로", "과의", "와의", "에는", "에도",
     "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만", "요"],
    key=len, reverse=True,
)

# ── 불용어: 질문 상투어·메타어 (본문 어디에나 있어 변별력 없는 말) ─────────────
_STOPWORDS = frozenset([
    "대한", "대해", "대해서", "관련", "관련해", "관련된", "어떤", "어떻게", "무엇",
    "무슨", "어느", "언제", "누가", "누구", "왜", "및", "또는", "그리고", "등",
    "있나", "있나요", "인가요", "한가요", "했나요", "됐나요", "주세요", "알려주세요",
    "말해줘", "무엇인가요", "어떠했나요", "밝혔나요", "보였나요", "취했나요",
    "발언", "입장", "논의", "언급", "질의", "질문", "답변", "주장", "요약", "정리",
    "내용", "여부", "구체적", "구체적인", "주요", "관한", "위원회에서", "회의에서",
])


def _safe_iso(year: int, month: int, day: int) -> str | None:
    """실존하는 날짜만 ISO 문자열로. "2025년 13월 40일" 같은 오타는 None —
    필터를 포기하고 일반 텍스트로 취급한다 (잘못된 날짜가 SQL 까지 가면 500)."""
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)).day


def _month_span(year: int, month: int) -> tuple[str, str] | None:
    if not 1 <= month <= 12:
        return None
    return (f"{year:04d}-{month:02d}-01",
            f"{year:04d}-{month:02d}-{_last_day_of_month(year, month):02d}")


def _find_dates(text: str) -> list[dict]:
    """텍스트 속 날짜 표현 전부 → [{span, from, to, year}] (등장 순).

    수집 순서: 연도 있는 형태 먼저 → 연도 없는 형태("9월 1일", "9월")는
    기존 매치와 겹치지 않을 때만, 앞선(없으면 뒤따르는) 연도를 물려받는다.
    실존하지 않는 날짜는 버린다 (필터 미적용 — 일반 텍스트 취급).
    """
    found: list[dict] = []

    def overlaps(s: int, e: int) -> bool:
        return any(not (e <= f["span"][0] or s >= f["span"][1]) for f in found)

    for m in _DATE_FULL_RE.finditer(text):
        iso = _safe_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            found.append({"span": m.span(), "from": iso, "to": iso, "year": int(m.group(1))})
    for m in _DATE_ISO_RE.finditer(text):
        if not overlaps(*m.span()):
            iso = _safe_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if iso:
                found.append({"span": m.span(), "from": iso, "to": iso, "year": int(m.group(1))})
    for m in _DATE_MONTH_RE.finditer(text):
        if not overlaps(*m.span()):
            span = _month_span(int(m.group(1)), int(m.group(2)))
            if span:
                found.append({"span": m.span(), "from": span[0], "to": span[1],
                              "year": int(m.group(1))})

    yearful = sorted(found, key=lambda f: f["span"][0])

    def inherit_year(pos: int) -> int | None:
        before = [f for f in yearful if f["span"][1] <= pos]
        after = [f for f in yearful if f["span"][0] >= pos]
        if before:
            return before[-1]["year"]
        return after[0]["year"] if after else None

    if yearful:  # 연도 없는 표현은 연도 있는 날짜가 함께 있을 때만 (오탐 방지)
        for m in _DATE_MD_RE.finditer(text):
            if not overlaps(*m.span()):
                y = inherit_year(m.start())
                iso = _safe_iso(y, int(m.group(1)), int(m.group(2))) if y else None
                if iso:
                    found.append({"span": m.span(), "from": iso, "to": iso, "year": y})
        for m in _DATE_M_RE.finditer(text):
            if not overlaps(*m.span()):
                y = inherit_year(m.start())
                span = _month_span(y, int(m.group(1))) if y else None
                if span:
                    found.append({"span": m.span(), "from": span[0], "to": span[1], "year": y})

    return sorted(found, key=lambda f: f["span"][0])


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """지정 구간들을 공백으로 치환 (replace 는 동일 문자열 오제거 위험)."""
    out, prev = [], 0
    for s, e in sorted(spans):
        out.append(text[prev:s])
        out.append(" ")
        prev = e
    out.append(text[prev:])
    return "".join(out)


def extract_filters(q: str):
    """질문 → (cleaned_q, committees, date_from, date_to). 못 찾으면 None.

    committees 는 리스트 (2026-07-03: 복수 위원회 질문 대응) — "외통위와 국방위 비교"에서
    첫 번째만 잡혀 나머지 위원회 근거가 검색에서 배제되던 문제 수정. 등장 순서 유지·중복 제거.
    """
    committees: list[str] | None = None
    date_from = None
    date_to = None
    cleaned = q

    # 날짜 표현 전부 수집 — 1개면 그 날짜(또는 월 범위), 2개 이상이면 기간으로 해석
    # ("2025년 7월 14일부터 9월 1일까지" 가 첫 날짜 하루로 축소돼 이후 근거가
    #  전부 배제 → 거짓 부정을 만들던 문제, 2026-07-07 수정)
    dates = _find_dates(cleaned)
    if dates:
        date_from = min(f["from"] for f in dates)
        date_to = max(f["to"] for f in dates)
        cleaned = _remove_spans(cleaned, [f["span"] for f in dates])

    # 위원회는 전부 감지 (findall) — 위원회명은 의미 정보라 질문에서 제거하지 않는다 (벡터 축에 유용)
    found = [COMMITTEE_MAP[m] for m in _COMMITTEE_RE.findall(cleaned)]
    if found:
        committees = list(dict.fromkeys(found))  # 등장 순서 유지 중복 제거

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, committees, date_from, date_to


def _strip_josa(token: str) -> str:
    """토큰 끝의 조사를 제거한다 (어간 2자 이상 보존)."""
    for josa in _JOSA:
        if token.endswith(josa) and len(token) - len(josa) >= 2:
            return token[: -len(josa)]
    return token


def content_tokens(q: str) -> list[str]:
    """조사·불용어·문장부호를 제거한 내용어 토큰 목록."""
    tokens = []
    for raw in q.split():
        tok = raw.strip(".,?!·'\"“”‘’()[]")
        if len(tok) < 2 or tok in _STOPWORDS:
            continue
        stem = _strip_josa(tok)
        if len(stem) < 2 or stem in _STOPWORDS:
            continue
        tokens.append(stem)
    return tokens
