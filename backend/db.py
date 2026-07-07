"""
DB 연결 풀 모듈.

- .env 의 DATABASE_URL 로 접속한다 (비밀값은 코드에 넣지 않는다).
- connection pool 은 작게 시작한다 (마스터 설계 문서 6-4:
  무료 클라우드 DB 는 동시 연결 수 제한이 낮으므로 min 1 / max 5).
- 사용법:
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
"""

import os
import time
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from psycopg2 import OperationalError
from psycopg2.pool import PoolError, ThreadedConnectionPool

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

_pool: ThreadedConnectionPool | None = None

POOL_WAIT_TIMEOUT = 10.0   # 풀 고갈 시 대기 한도(초) — 초과할 때만 오류
_POOL_WAIT_STEP   = 0.05


def init_pool(minconn: int = 1, maxconn: int = 5) -> None:
    """앱 시작 시 1회 호출. DATABASE_URL 이 없으면 명확한 오류를 낸다."""
    global _pool
    if _pool is not None:
        return
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(".env 에 DATABASE_URL 이 없습니다.")
    _pool = ThreadedConnectionPool(minconn, maxconn, dsn=db_url)


def close_pool() -> None:
    """앱 종료 시 1회 호출."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


def _checkout():
    """살아있는 연결을 빌린다.

    - 풀 고갈: psycopg2 getconn 은 대기 없이 즉시 PoolError 를 던진다 —
      동시 요청 6개부터 500 이 나던 원인. 한도 내에서 짧게 재시도 대기.
    - 죽은 연결: 무료 클라우드 DB 가 유휴 연결을 끊으면 풀에 시체가 남아
      그걸 뽑는 요청마다 500 — 대여 시 SELECT 1 로 확인하고 시체는 폐기.
    """
    if _pool is None:
        raise RuntimeError("connection pool 이 초기화되지 않았습니다 (init_pool 필요).")
    deadline = time.monotonic() + POOL_WAIT_TIMEOUT
    while True:
        try:
            conn = _pool.getconn()
        except PoolError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(_POOL_WAIT_STEP)
            continue
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        except OperationalError:
            _pool.putconn(conn, close=True)  # 죽은 연결 폐기 후 다른 연결 시도
            if time.monotonic() >= deadline:
                raise


@contextmanager
def get_conn():
    """풀에서 연결을 빌려 쓰고 반드시 반납한다. 예외 시 rollback, 죽은 연결은 폐기."""
    conn = _checkout()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            conn.close()  # rollback 조차 안 되는 연결 — 반납 시 폐기되도록 닫는다
        raise
    finally:
        _pool.putconn(conn, close=bool(conn.closed))
