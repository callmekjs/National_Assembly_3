"""배포 방어선 (4단계-A) — IP rate limit(1차) + 일별 OpenAI 비용 상한(2차).

의존성 없이 자체 구현. 인메모리 슬라이딩 윈도우 — 단일 인스턴스 전제(Render free 1대),
재시작 시 리셋. XFF 스푸핑·분산 공격의 완전 방어가 목적이 아니라 **비용 사고 방지**가
목적 — 1차가 뚫려도 2차(비용 상한)가 지갑을 지킨다.
스펙: docs/superpowers/specs/2026-07-11-dep-a-guardrails-design.md
"""
import time
from collections import deque

_WINDOW_SEC = 60.0
_COST_CACHE_SEC = 60.0

_cost_cache: tuple[float, float] | None = None  # (계산 시각, 오늘 합계 USD)


class RateLimiter:
    """키(IP)별 슬라이딩 윈도우 카운터. now 주입으로 시계 없이 테스트 가능.

    메모리: 키는 고유 클라이언트 키 수만큼 자람. XFF 위조로 임의 키를 무한 생성하는
    메모리 공격은 client_ip 의 키 절단(64자)이 항목 크기를 상한 — 빈 키 GC 는 생략(단순성).
    """

    def __init__(self, per_min: int):
        self.per_min = per_min
        self._hits: dict[str, deque] = {}

    def allow(self, key: str, now: float) -> bool:
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= _WINDOW_SEC:
            q.popleft()
        if len(q) >= self.per_min:
            return False
        q.append(now)
        return True


def client_ip(xff: str | None, fallback: str) -> str:
    """X-Forwarded-For 첫 값(플랫폼 프록시 전제) → 없으면 직접 연결 주소.

    64자 절단 — 위조 XFF 로 거대 키를 만들어 리미터 메모리를 불리는 것을 상한."""
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first[:64]
    return fallback


def daily_cost_today() -> float:
    """오늘(UTC 자정 기준) query_logs 누적 비용 USD. usage NULL(사전차단) 행 제외."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(SUM((usage->>'est_cost_usd')::float), 0)
            FROM query_logs
            WHERE created_at >= date_trunc('day', now()) AND usage IS NOT NULL
        """)
        return float(cur.fetchone()[0])


def daily_cost_exceeded(limit: float, now: float | None = None, fetch=None) -> bool:
    """오늘 비용 >= limit 인가. 60초 캐시로 요청마다 DB 를 때리지 않는다.

    ±60초 오버슛은 허용 오차(분당 한도 × 질의당 ~$0.01 수준). now·fetch 는 테스트 주입용.
    /answer 는 query_logs 미기록이라 집계 밖 — 배포 프론트는 /query 만 사용(스펙 한계 참조).
    """
    global _cost_cache
    if now is None:
        now = time.time()
    if fetch is None:
        fetch = daily_cost_today
    if _cost_cache is None or now - _cost_cache[0] >= _COST_CACHE_SEC:
        _cost_cache = (now, fetch())
    return _cost_cache[1] >= limit


def reset_cost_cache() -> None:
    """테스트·수동 운영용 캐시 초기화."""
    global _cost_cache
    _cost_cache = None
