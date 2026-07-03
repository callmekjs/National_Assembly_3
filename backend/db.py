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
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from psycopg2.pool import ThreadedConnectionPool

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

_pool: ThreadedConnectionPool | None = None


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


@contextmanager
def get_conn():
    """풀에서 연결을 빌려 쓰고 반드시 반납한다. 예외 시 rollback."""
    if _pool is None:
        raise RuntimeError("connection pool 이 초기화되지 않았습니다 (init_pool 필요).")
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)
