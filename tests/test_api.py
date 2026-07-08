"""HTTP API 계층 테스트 — FastAPI TestClient 로 엔드포인트 오케스트레이션 검증.

로직 테스트(test_answer 등)가 못 보는 이음새를 커버:
    1. 입력 검증 → 422 (날짜·rating·question 길이)
    2. /query 사전차단 경로 (검색 0건 → LLM 미호출 고정 문구 + 로그)
    3. OpenAIError → 502 매핑 (프론트 api.js 가 이 코드에 의존)
    4. /feedback UUID 검증 422·미존재 404, /citations 404
    5. /health

DB(로컬 Docker)가 없으면 전부 건너뜀 — CI 러너 대응. LLM 은 호출하지 않는다.
실행: python tests/test_api.py  (pytest 도 지원)
"""

import io
import sys
import uuid
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import db  # noqa: E402

try:
    db.init_pool()
    with db.get_conn() as _conn:
        pass
    HAS_DB = True
except Exception:
    HAS_DB = False

if HAS_DB:
    from fastapi.testclient import TestClient
    from openai import OpenAIError

    import main
    client = TestClient(main.app)

_SKIP_MSG = "  - DB 없음 — 건너뜀 (로컬 Docker 필요)"


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_health():
    if not HAS_DB:
        print(_SKIP_MSG)
        return
    r = client.get("/health")
    check("health: 200 + db ok", r.status_code == 200 and r.json()["db"] == "ok", r.json())


def test_validation_422():
    if not HAS_DB:
        print(_SKIP_MSG)
        return
    r = client.post("/query", json={"question": "인공지능", "date_from": "2025-13-01"})
    check("검증: 잘못된 날짜 422", r.status_code == 422, r.status_code)
    r = client.post("/query", json={"question": "a"})
    check("검증: 질문 1자 422", r.status_code == 422, r.status_code)
    r = client.post("/query", json={"question": "가" * 1001})
    check("검증: 질문 1001자 422", r.status_code == 422, r.status_code)
    r = client.get("/meetings", params={"date_from": "abc"})
    check("검증: /meetings 날짜 422", r.status_code == 422, r.status_code)
    r = client.post("/feedback", json={"query_id": str(uuid.uuid4()), "rating": 999})
    check("검증: rating 999 → 422", r.status_code == 422, r.status_code)


def test_query_pre_gate_none():
    """검색 0건 → LLM 미호출 고정 문구 + grounding NONE + query_id 발급."""
    if not HAS_DB:
        print(_SKIP_MSG)
        return

    def no_hits(*a, **kw):
        return []

    orig = main.hybrid_search
    main.hybrid_search = no_hits
    try:
        r = client.post("/query", json={"question": "존재하지 않는 주제의 질문"})
    finally:
        main.hybrid_search = orig
    body = r.json()
    check("사전차단: 200 + NONE", r.status_code == 200 and body["grounding"] == "NONE", body.get("grounding"))
    check("사전차단: 고정 문구", "확인할 수 없습니다" in body["answer"], body["answer"])
    check("사전차단: sources 빈 목록", body["sources"] == [])
    check("사전차단: query_id 발급 (로그 저장)", body.get("query_id"), body.get("query_id"))


def test_openai_error_502():
    """임베딩/LLM 장애 → 502 (프론트 api.js 의 친화 메시지 분기가 의존)."""
    if not HAS_DB:
        print(_SKIP_MSG)
        return

    def boom(*a, **kw):
        raise OpenAIError("simulated outage")

    orig = main.hybrid_search
    main.hybrid_search = boom
    try:
        r = client.post("/query", json={"question": "인공지능 기본법 논의"})
    finally:
        main.hybrid_search = orig
    check("장애: OpenAIError → 502", r.status_code == 502, r.status_code)
    check("장애: detail 에 원인", "실패" in r.json()["detail"], r.json())


def test_feedback_and_citation_errors():
    if not HAS_DB:
        print(_SKIP_MSG)
        return
    r = client.post("/feedback", json={"query_id": "not-a-uuid", "rating": 5})
    check("피드백: UUID 형식 아님 → 422", r.status_code == 422, r.status_code)
    r = client.post("/feedback", json={"query_id": str(uuid.uuid4()), "rating": 5})
    check("피드백: 미존재 query_id → 404", r.status_code == 404, r.status_code)
    r = client.get("/citations/없는_청크_id")
    check("출처: 미존재 chunk_id → 404", r.status_code == 404, r.status_code)


def test_issues_list():
    """이슈 목록 — 사전이 비어 있어도 200 + issues 키 (스키마만 보장)."""
    if not HAS_DB:
        print(_SKIP_MSG)
        return
    r = client.get("/issues")
    check("issues: 200", r.status_code == 200, r.status_code)
    body = r.json()
    check("issues: 목록 키", isinstance(body.get("issues"), list), body)
    if body["issues"]:
        first = body["issues"][0]
        check("issues: 필드", all(k in first for k in
              ("issue_id", "title", "type", "description", "chunk_count", "turn_count",
               "core_chunk_count")), first)


def main_():
    test_health()
    test_validation_422()
    test_query_pre_gate_none()
    test_openai_error_502()
    test_feedback_and_citation_errors()
    test_issues_list()
    print("\nALL PASS" if HAS_DB else "\nDB 없음 — 전체 건너뜀")


if __name__ == "__main__":
    main_()
