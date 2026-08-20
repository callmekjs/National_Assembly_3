from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "sample_sources_db.py"
SPEC = importlib.util.spec_from_file_location("sample_sources_db_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_db_sampler_query_is_source_disjoint_and_deterministic(monkeypatch):
    executed = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params):
            executed["sql"] = sql
            executed["params"] = params

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self, **_):
            return Cursor()

    monkeypatch.setattr(MODULE.psycopg2, "connect", lambda _: Connection())
    assert MODULE.fetch_candidates("db", {"old-b", "old-a"}, 17, 5000) == []
    assert "NOT (ch.source_id = ANY(%s))" in executed["sql"]
    assert "ORDER BY md5(ch.chunk_id || %s)" in executed["sql"]
    assert executed["params"] == (["old-a", "old-b"], "17", 5000)


def test_authoring_risk_is_removed_before_balanced_sampling():
    records = [
        {"text": "아까 그 부분은 다시 확인하겠습니다."},
        {"text": "예산은 10억 원에서 12억 원으로 증가했습니다."},
    ]
    assert MODULE.authoring_safe(records) == [records[1]]
