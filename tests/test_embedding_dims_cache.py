"""embedding_dims 캐시 회귀 테스트 (감사 2026-08-14).

예전 결함: @lru_cache 가 **조회 실패 시의 기본값(1536)까지 캐시**했다.
콜드스타트 중 DB 가 한 번 삐끗하면 512차원 배포본에 1536차원 질의 벡터를 계속 만들어
이후 전 질의가 pgvector 차원 불일치로 500 이 됐고, DB 가 회복돼도 프로세스 재시작
전까지 풀리지 않았다.

계약: 성공한 조회만 캐시한다. 실패는 캐시하지 않아 다음 호출에서 다시 시도한다.
"""

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import search_vector  # noqa: E402


class _Cur:
    def __init__(self, row):
        self._row = row

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, row):
        self._row = row

    def cursor(self, *a, **k):
        return _Cur(self._row)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _conn_returning(row, calls):
    @contextmanager
    def _f():
        calls.append("db")
        yield _Conn(row)

    return _f


def _conn_failing(calls):
    @contextmanager
    def _f():
        calls.append("db")
        raise RuntimeError("connection pool exhausted")
        yield  # pragma: no cover

    return _f


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """매 테스트마다 캐시와 환경변수를 초기화한다."""
    monkeypatch.delenv("EMBEDDING_DIMENSIONS", raising=False)
    search_vector._dims_cache = None
    yield
    search_vector._dims_cache = None


def test_조회_실패는_캐시되지_않는다(monkeypatch):
    """실패 후 DB 가 회복되면 재시작 없이 올바른 차원을 읽어야 한다."""
    calls = []
    monkeypatch.setattr(search_vector, "get_conn", _conn_failing(calls))
    assert search_vector.embedding_dims() == 1536  # 기본값으로 버틴다
    assert search_vector._dims_cache is None       # 그러나 굳히지 않는다

    # DB 회복 — 재시작 없이 실제 차원을 읽어야 한다
    monkeypatch.setattr(search_vector, "get_conn", _conn_returning((512,), calls))
    assert search_vector.embedding_dims() == 512
    assert len(calls) == 2                          # 실패 후 실제로 재조회했다


def test_성공한_조회는_캐시된다(monkeypatch):
    """정상 경로에서는 매 질의마다 DB 를 치지 않는다."""
    calls = []
    monkeypatch.setattr(search_vector, "get_conn", _conn_returning((512,), calls))
    assert search_vector.embedding_dims() == 512
    assert search_vector.embedding_dims() == 512
    assert len(calls) == 1


def test_빈_테이블도_캐시되지_않는다(monkeypatch):
    """초기 적재 중(행 0건)에 기본값이 굳으면 적재 후에도 안 풀린다."""
    calls = []
    monkeypatch.setattr(search_vector, "get_conn", _conn_returning(None, calls))
    assert search_vector.embedding_dims() == 1536
    assert search_vector._dims_cache is None

    monkeypatch.setattr(search_vector, "get_conn", _conn_returning((512,), calls))
    assert search_vector.embedding_dims() == 512


def test_환경변수_오버라이드가_DB보다_우선(monkeypatch):
    calls = []
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "256")
    monkeypatch.setattr(search_vector, "get_conn", _conn_returning((512,), calls))
    assert search_vector.embedding_dims() == 256
    assert calls == []  # DB 를 아예 보지 않는다


def test_차원_확정_시_질의벡터_캐시를_비운다(monkeypatch):
    """장애 중 기본 차원으로 만든 벡터가 남으면 그 질문만 계속 깨진다."""
    calls = []
    search_vector.embed_query.cache_clear()
    monkeypatch.setattr(search_vector, "get_conn", _conn_returning((512,), calls))

    cleared = []
    monkeypatch.setattr(
        search_vector.embed_query, "cache_clear", lambda: cleared.append(1)
    )
    search_vector.embedding_dims()
    assert cleared == [1]
