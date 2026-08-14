"""파서 재처리 적재 시 동일 발언의 DB 연관 데이터 보존 회귀 테스트."""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

psycopg2 = pytest.importorskip("psycopg2")
from dotenv import load_dotenv  # noqa: E402
from psycopg2.extras import Json, execute_values  # noqa: E402

from jsonl_to_postgres import CHUNK_COLUMNS, insert_chunks  # noqa: E402


def _chunk(turn_no: int, text: str, page: int) -> dict:
    turn_id = f"test_loader_turn_{turn_no:04d}"
    return {
        "chunk_id": f"{turn_id}_chunk_001",
        "turn_id": turn_id,
        "chunk_type": "utterance",
        "chunk_index": 1,
        "chunk_total": 1,
        "source_id": "test_loader_source",
        "committee_id": None,
        "meeting_date": "2026-08-14",
        "speaker": "테스트",
        "role": "진술인",
        "page_start": page,
        "page_end": page,
        "text": text,
        "context_before": "",
        "context_after": "",
        "embed_text": f"테스트위원회 2026-08-14 테스트 진술인 발언: {text}",
        "is_short": True,
        "policy_domain": "기타",
        "bill_refs": [],
        "utterance_type": "statement",
        "stance_signals": "neutral",
        "mentions": [],
        "parser_version": "v1.3",
        "chunker_version": "v1.1",
    }


def _insert_raw_chunks(cur, rows: list[dict]) -> None:
    values = []
    for c in rows:
        values.append(tuple(
            Json(c[key]) if key in {"bill_refs", "mentions"} else c.get(key)
            for key in CHUNK_COLUMNS
        ))
    execute_values(
        cur,
        f"INSERT INTO chunks ({', '.join(CHUNK_COLUMNS)}) VALUES %s",
        values,
    )


def test_insert_chunks_rekeys_only_semantically_identical_relations():
    load_dotenv(ROOT / ".env")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL 없음")

    try:
        conn = psycopg2.connect(db_url)
    except psycopg2.Error as exc:
        pytest.skip(f"PostgreSQL 연결 불가: {exc}")

    old_a = _chunk(1, "보존할 첫 발언", 1)
    old_garbage = _chunk(2, "산회 뒤 증인 명단", 2)
    old_c = _chunk(3, "번호만 바뀔 실제 발언", 3)
    new_a = _chunk(1, "보존할 첫 발언", 1)
    new_c = _chunk(2, "번호만 바뀔 실제 발언", 3)
    new_x = _chunk(3, "새로 복구된 전문가 발언", 4)

    try:
        with conn.cursor() as cur:
            # public 데이터를 전혀 건드리지 않도록 같은 이름의 TEMP 테이블로 가린다.
            for table in (
                "chunks", "embeddings_openai", "issue_chunks",
                "issue_stances", "utterance_summaries",
            ):
                cur.execute(
                    f"CREATE TEMP TABLE {table} "
                    f"(LIKE public.{table} INCLUDING ALL) ON COMMIT DROP"
                )
            # LIKE는 FK를 복사하지 않으므로 운영 스키마의 CASCADE만 재현한다.
            cur.execute(
                "ALTER TABLE embeddings_openai ADD FOREIGN KEY (chunk_id) "
                "REFERENCES chunks(chunk_id) ON DELETE CASCADE"
            )
            cur.execute(
                "ALTER TABLE issue_chunks ADD FOREIGN KEY (chunk_id) "
                "REFERENCES chunks(chunk_id) ON DELETE CASCADE"
            )

            _insert_raw_chunks(cur, [old_a, old_garbage, old_c])
            cur.executemany(
                "INSERT INTO embeddings_openai (chunk_id, embedding, model) "
                "VALUES (%s, NULL, 'test-model')",
                [(c["chunk_id"],) for c in (old_a, old_garbage, old_c)],
            )
            cur.executemany(
                "INSERT INTO issue_chunks "
                "(issue_id, chunk_id, turn_id, judge, map_version) "
                "VALUES ('test-issue', %s, %s, 'llm_core', 'test-v1')",
                [(c["chunk_id"], c["turn_id"]) for c in (old_a, old_garbage, old_c)],
            )
            cur.executemany(
                "INSERT INTO issue_stances "
                "(issue_id, turn_id, speaker, role, stance, judge_model, map_version) "
                "VALUES ('test-issue', %s, '테스트', '진술인', 'neutral', "
                "'test-model', 'test-v1')",
                [(c["turn_id"],) for c in (old_a, old_garbage, old_c)],
            )
            cur.executemany(
                "INSERT INTO utterance_summaries (chunk_id, summary) VALUES (%s, %s)",
                [(c["chunk_id"], f"요약 {i}")
                 for i, c in enumerate((old_a, old_garbage, old_c), start=1)],
            )

            stats = insert_chunks(cur, [new_a, new_c, new_x], committee_id=None)

            assert stats == {
                "emb_restored": 2,
                "emb_lost": 1,
                "issue_restored": 2,
                "issue_lost": 1,
                "stance_restored": 2,
                "stance_lost": 1,
                "summary_restored": 2,
                "summary_lost": 1,
                "emb_saved": 3,
            }

            cur.execute("SELECT chunk_id FROM embeddings_openai ORDER BY chunk_id")
            assert [r[0] for r in cur.fetchall()] == [new_a["chunk_id"], new_c["chunk_id"]]

            cur.execute("SELECT chunk_id, turn_id FROM issue_chunks ORDER BY turn_id")
            assert cur.fetchall() == [
                (new_a["chunk_id"], new_a["turn_id"]),
                (new_c["chunk_id"], new_c["turn_id"]),
            ]

            cur.execute("SELECT turn_id FROM issue_stances ORDER BY turn_id")
            assert [r[0] for r in cur.fetchall()] == [new_a["turn_id"], new_c["turn_id"]]

            cur.execute("SELECT chunk_id FROM utterance_summaries ORDER BY chunk_id")
            assert [r[0] for r in cur.fetchall()] == [new_a["chunk_id"], new_c["chunk_id"]]
    finally:
        conn.rollback()
        conn.close()
