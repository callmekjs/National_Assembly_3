"""
[7] jsonl_to_postgres
chunks_v1.jsonl 을 PostgreSQL 에 적재한다. (ETL-7)

필수 환경 변수 (.env):
    DATABASE_URL — 예: postgresql://postgres:password@localhost:5432/national_assembly

동작:
    1. db/schema.sql 로 스키마 보장 (IF NOT EXISTS, 반복 안전)
    2. source(회의)별로:
         - committees / meetings upsert
         - 인라인 품질 체크 (meeting_date·speaker·빈 텍스트 비율)
         - chunks 는 source_id 기준 DELETE 후 재삽입 (재실행 안전)
         - 적재 직후 행 수 검증 (JSONL 줄 수 == DB 행 수)
    3. 전체 종료 후 speakers 를 chunks 에서 집계로 재생성
    4. 요약 리포트 출력

실행:
    python scripts/jsonl_to_postgres.py              # 전체
    python scripts/jsonl_to_postgres.py 기재위        # 특정 위원회(폴더 약칭)만
    python scripts/jsonl_to_postgres.py 과방위 외통위
"""

import io
import json
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values
from dotenv import load_dotenv

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent
CHUNKS_ROOT  = PROJECT_ROOT / "data" / "v1" / "chunks"
SCHEMA_SQL   = PROJECT_ROOT / "db" / "schema.sql"

# source 를 건너뛰는 임계값 (적재 전 안전장치)
MAX_MEETING_DATE_NULL_RATIO = 0.50   # meeting_date 결측 50% 초과 → skip
MAX_EMPTY_TEXT_RATIO        = 0.20   # 빈 본문 20% 초과 → skip

# chunks 테이블에 넣을 컬럼 순서 (INSERT 와 동일하게 유지)
CHUNK_COLUMNS = [
    "chunk_id", "turn_id", "chunk_type", "chunk_index", "chunk_total",
    "source_id", "committee_id", "meeting_date", "speaker", "role",
    "page_start", "page_end", "text", "context_before", "context_after",
    "embed_text", "is_short", "policy_domain", "bill_refs", "utterance_type",
    "stance_signals", "mentions", "parser_version", "chunker_version",
]


def _nullify(value):
    """빈 문자열은 None 으로 (DATE 등 타입 오류 방지)."""
    if value == "":
        return None
    return value


def read_chunks(source_id: str) -> list[dict]:
    path = CHUNKS_ROOT / source_id / "chunks_v1.jsonl"
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def quality_check(chunks: list[dict]) -> dict:
    total = len(chunks)
    md_null = sum(1 for c in chunks if not c.get("meeting_date"))
    sp_null = sum(1 for c in chunks if not c.get("speaker"))
    txt_empty = sum(1 for c in chunks if not (c.get("text") or "").strip())
    return {
        "total": total,
        "meeting_date_null_ratio": md_null / total if total else 0.0,
        "speaker_null_ratio":      sp_null / total if total else 0.0,
        "empty_text_ratio":        txt_empty / total if total else 0.0,
    }


def upsert_committee(cur, chunk: dict) -> int | None:
    """chunk 의 folder(약칭)/committee(정식명)/policy_domain 으로 committees upsert → id 반환."""
    name = chunk.get("folder")
    if not name:
        return None
    cur.execute(
        """
        INSERT INTO committees (name, full_name, policy_domain)
        VALUES (%s, %s, %s)
        ON CONFLICT (name) DO UPDATE
          SET full_name     = EXCLUDED.full_name,
              policy_domain = EXCLUDED.policy_domain
        RETURNING committee_id
        """,
        (name, chunk.get("committee"), chunk.get("policy_domain")),
    )
    return cur.fetchone()[0]


def upsert_meeting(cur, source_id: str, committee_id: int | None, chunk: dict) -> None:
    cur.execute(
        """
        INSERT INTO meetings (source_id, committee_id, file_name, meeting_date)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (source_id) DO UPDATE
          SET committee_id = EXCLUDED.committee_id,
              file_name    = EXCLUDED.file_name,
              meeting_date = EXCLUDED.meeting_date
        """,
        (source_id, committee_id, chunk.get("file_name"), _nullify(chunk.get("meeting_date"))),
    )


def insert_chunks(cur, chunks: list[dict], committee_id: int | None) -> dict[str, int]:
    """chunks 를 DELETE 후 재삽입하고 동일 발언의 연관 데이터를 보존한다.

    파서 재처리로 중간 turn 이 추가·삭제되면 뒤쪽 chunk_id/turn_id 가 밀린다.
    단순 DELETE→INSERT 는 임베딩뿐 아니라 issue_chunks 를 CASCADE 삭제하고,
    FK가 없는 issue_stances·utterance_summaries 는 낡은 ID로 남긴다.

    그래서 embed_text·본문·페이지·청크 순서가 같은 행을 occurrence 순서로 1:1
    대응시킨 뒤 임베딩·쟁점 매핑·입장·요약을 새 ID로 옮긴다. 내용이 달라진 행은
    일부러 복원하지 않아 새 임베딩/판정 대상으로 남긴다.
    """
    source_id = chunks[0]["source_id"]

    for temp_table in (
        "_old_chunk_keys", "_new_chunk_keys", "_chunk_id_map", "_turn_id_map",
        "_emb_backup", "_issue_chunks_backup", "_stances_backup", "_summaries_backup",
    ):
        cur.execute(f"DROP TABLE IF EXISTS {temp_table}")

    # 같은 짧은 답변(예: "예.")이 한 페이지에 반복될 수 있어 semantic_key만으로
    # 조인하지 않고, 같은 key 안의 occurrence 번호까지 붙여 1:1로 대응한다.
    cur.execute(
        """
        CREATE TEMP TABLE _old_chunk_keys ON COMMIT DROP AS
        WITH keyed AS (
          SELECT ch.chunk_id AS old_chunk_id, ch.turn_id AS old_turn_id,
                 md5(jsonb_build_array(
                     ch.embed_text, ch.text, ch.speaker, ch.role,
                     ch.page_start, ch.page_end, ch.chunk_index, ch.chunk_total
                 )::text) AS semantic_key
          FROM chunks ch
          WHERE ch.source_id = %s
        )
        SELECT old_chunk_id, old_turn_id, semantic_key,
               row_number() OVER (
                   PARTITION BY semantic_key ORDER BY old_turn_id, old_chunk_id
               ) AS occurrence
        FROM keyed
        """,
        (source_id,),
    )

    cur.execute(
        """
        CREATE TEMP TABLE _emb_backup ON COMMIT DROP AS
        SELECT k.old_chunk_id, e.embedding, e.model, e.created_at
        FROM _old_chunk_keys k
        JOIN embeddings_openai e ON e.chunk_id = k.old_chunk_id
        """
    )
    emb_saved = cur.rowcount
    cur.execute(
        """
        CREATE TEMP TABLE _issue_chunks_backup ON COMMIT DROP AS
        SELECT k.old_chunk_id, ic.issue_id, ic.vec_score, ic.kw_hit,
               ic.judge, ic.map_version, ic.mapped_at
        FROM _old_chunk_keys k
        JOIN issue_chunks ic ON ic.chunk_id = k.old_chunk_id
        """
    )
    issue_saved = cur.rowcount
    cur.execute(
        """
        CREATE TEMP TABLE _stances_backup ON COMMIT DROP AS
        SELECT s.issue_id, s.turn_id AS old_turn_id, s.speaker, s.role,
               s.stance, s.judge_model, s.map_version, s.mapped_at
        FROM issue_stances s
        WHERE s.turn_id IN (SELECT DISTINCT old_turn_id FROM _old_chunk_keys)
        """
    )
    stance_saved = cur.rowcount
    cur.execute(
        """
        CREATE TEMP TABLE _summaries_backup ON COMMIT DROP AS
        SELECT k.old_chunk_id, s.summary, s.created_at
        FROM _old_chunk_keys k
        JOIN utterance_summaries s ON s.chunk_id = k.old_chunk_id
        """
    )
    summary_saved = cur.rowcount

    # FK가 없어 자동 삭제되지 않는 캐시는 먼저 명시적으로 무효화한다.
    cur.execute(
        "DELETE FROM issue_stances WHERE turn_id IN "
        "(SELECT DISTINCT old_turn_id FROM _old_chunk_keys)"
    )
    cur.execute(
        "DELETE FROM utterance_summaries WHERE chunk_id IN "
        "(SELECT old_chunk_id FROM _old_chunk_keys)"
    )

    cur.execute("DELETE FROM chunks WHERE source_id = %s", (source_id,))

    rows = []
    for c in chunks:
        rows.append((
            c.get("chunk_id"), c.get("turn_id"), c.get("chunk_type"),
            c.get("chunk_index"), c.get("chunk_total"), c.get("source_id"),
            committee_id, _nullify(c.get("meeting_date")), c.get("speaker"), c.get("role"),
            c.get("page_start"), c.get("page_end"), c.get("text"),
            c.get("context_before"), c.get("context_after"), c.get("embed_text"),
            c.get("is_short"), c.get("policy_domain"), Json(c.get("bill_refs") or []),
            c.get("utterance_type"), c.get("stance_signals"), Json(c.get("mentions") or []),
            c.get("parser_version"), c.get("chunker_version"),
        ))

    cols = ", ".join(CHUNK_COLUMNS)
    execute_values(
        cur,
        f"INSERT INTO chunks ({cols}) VALUES %s",
        rows,
        page_size=1000,
    )

    cur.execute(
        """
        CREATE TEMP TABLE _new_chunk_keys ON COMMIT DROP AS
        WITH keyed AS (
          SELECT ch.chunk_id AS new_chunk_id, ch.turn_id AS new_turn_id,
                 md5(jsonb_build_array(
                     ch.embed_text, ch.text, ch.speaker, ch.role,
                     ch.page_start, ch.page_end, ch.chunk_index, ch.chunk_total
                 )::text) AS semantic_key
          FROM chunks ch
          WHERE ch.source_id = %s
        )
        SELECT new_chunk_id, new_turn_id, semantic_key,
               row_number() OVER (
                   PARTITION BY semantic_key ORDER BY new_turn_id, new_chunk_id
               ) AS occurrence
        FROM keyed
        """,
        (source_id,),
    )
    cur.execute(
        """
        CREATE TEMP TABLE _chunk_id_map ON COMMIT DROP AS
        SELECT o.old_chunk_id, n.new_chunk_id, o.old_turn_id, n.new_turn_id
        FROM _old_chunk_keys o
        JOIN _new_chunk_keys n USING (semantic_key, occurrence)
        """
    )
    cur.execute(
        """
        CREATE TEMP TABLE _turn_id_map ON COMMIT DROP AS
        WITH pairs AS (
          SELECT old_turn_id, new_turn_id, count(*) AS matched
          FROM _chunk_id_map
          WHERE old_turn_id IS NOT NULL AND new_turn_id IS NOT NULL
          GROUP BY old_turn_id, new_turn_id
        ), old_counts AS (
          SELECT old_turn_id, count(*) AS n FROM _old_chunk_keys GROUP BY old_turn_id
        ), new_counts AS (
          SELECT new_turn_id, count(*) AS n FROM _new_chunk_keys GROUP BY new_turn_id
        )
        SELECT p.old_turn_id, p.new_turn_id
        FROM pairs p
        JOIN old_counts o USING (old_turn_id)
        JOIN new_counts n USING (new_turn_id)
        WHERE p.matched = o.n AND p.matched = n.n
          AND NOT EXISTS (
              SELECT 1 FROM pairs p2
              WHERE p2.old_turn_id = p.old_turn_id AND p2.new_turn_id <> p.new_turn_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM pairs p2
              WHERE p2.new_turn_id = p.new_turn_id AND p2.old_turn_id <> p.old_turn_id
          )
        """
    )

    # 의미가 같은 청크만 기존 벡터를 새 ID로 복원한다.
    cur.execute(
        """
        INSERT INTO embeddings_openai (chunk_id, embedding, model, created_at)
        SELECT m.new_chunk_id, b.embedding, b.model, b.created_at
        FROM _emb_backup b
        JOIN _chunk_id_map m USING (old_chunk_id)
        """,
    )
    emb_restored = cur.rowcount

    cur.execute(
        """
        INSERT INTO issue_chunks
          (issue_id, chunk_id, turn_id, vec_score, kw_hit, judge, map_version, mapped_at)
        SELECT b.issue_id, m.new_chunk_id, m.new_turn_id, b.vec_score, b.kw_hit,
               b.judge, b.map_version, b.mapped_at
        FROM _issue_chunks_backup b
        JOIN _chunk_id_map m USING (old_chunk_id)
        ON CONFLICT (issue_id, chunk_id) DO NOTHING
        """
    )
    issue_restored = cur.rowcount

    cur.execute(
        """
        INSERT INTO issue_stances
          (issue_id, turn_id, speaker, role, stance, judge_model, map_version, mapped_at)
        SELECT b.issue_id, m.new_turn_id, b.speaker, b.role, b.stance,
               b.judge_model, b.map_version, b.mapped_at
        FROM _stances_backup b
        JOIN _turn_id_map m USING (old_turn_id)
        ON CONFLICT (issue_id, turn_id) DO UPDATE SET
          speaker = EXCLUDED.speaker, role = EXCLUDED.role, stance = EXCLUDED.stance,
          judge_model = EXCLUDED.judge_model, map_version = EXCLUDED.map_version,
          mapped_at = EXCLUDED.mapped_at
        """
    )
    stance_restored = cur.rowcount

    cur.execute(
        """
        INSERT INTO utterance_summaries (chunk_id, summary, created_at)
        SELECT m.new_chunk_id, b.summary, b.created_at
        FROM _summaries_backup b
        JOIN _chunk_id_map m USING (old_chunk_id)
        ON CONFLICT (chunk_id) DO NOTHING
        """
    )
    summary_restored = cur.rowcount

    return {
        "emb_restored": emb_restored,
        "emb_lost": len(chunks) - emb_restored,
        "issue_restored": issue_restored,
        "issue_lost": issue_saved - issue_restored,
        "stance_restored": stance_restored,
        "stance_lost": stance_saved - stance_restored,
        "summary_restored": summary_restored,
        "summary_lost": summary_saved - summary_restored,
        "emb_saved": emb_saved,
    }


def rebuild_speakers(cur) -> int:
    """chunks 전체에서 발언자를 집계로 재생성 (항상 정확·재실행 안전)."""
    cur.execute("DELETE FROM speakers")
    cur.execute(
        """
        INSERT INTO speakers (name, role, committee_id, utterance_count)
        SELECT speaker, role, committee_id, COUNT(*)
        FROM chunks
        WHERE speaker IS NOT NULL AND speaker <> ''
        GROUP BY speaker, role, committee_id
        """
    )
    cur.execute("SELECT COUNT(*) FROM speakers")
    return cur.fetchone()[0]


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[ERROR] .env 에 DATABASE_URL 이 없습니다.")
        sys.exit(1)

    if not CHUNKS_ROOT.exists():
        print(f"[ERROR] 청크 데이터 없음: {CHUNKS_ROOT}")
        print("먼저 chunker_v1.py 를 실행하세요.")
        sys.exit(1)

    targets = set(sys.argv[1:])
    source_ids = sorted(p.name for p in CHUNKS_ROOT.iterdir() if p.is_dir())
    if targets:
        source_ids = [s for s in source_ids if any(s.startswith(t) for t in targets)]
    if not source_ids:
        print("[ERROR] 대상 source 가 없습니다.")
        sys.exit(1)

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    print(f"접속 성공. 대상 source: {len(source_ids)}개\n")

    # 스키마 보장
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.commit()

    total_chunks = 0
    loaded_sources = 0
    emb_restored = 0
    emb_lost = 0
    linked_totals = {
        "issue_restored": 0, "issue_lost": 0,
        "stance_restored": 0, "stance_lost": 0,
        "summary_restored": 0, "summary_lost": 0,
    }
    skipped = []
    mismatches = []

    for i, sid in enumerate(source_ids, start=1):
        chunks = read_chunks(sid)
        if not chunks:
            skipped.append((sid, "빈 파일"))
            continue

        q = quality_check(chunks)
        if q["meeting_date_null_ratio"] > MAX_MEETING_DATE_NULL_RATIO:
            skipped.append((sid, f"meeting_date 결측 {q['meeting_date_null_ratio']:.0%}"))
            continue
        if q["empty_text_ratio"] > MAX_EMPTY_TEXT_RATIO:
            skipped.append((sid, f"빈 본문 {q['empty_text_ratio']:.0%}"))
            continue

        try:
            with conn.cursor() as cur:
                committee_id = upsert_committee(cur, chunks[0])
                upsert_meeting(cur, sid, committee_id, chunks[0])
                stats = insert_chunks(cur, chunks, committee_id)
                # 행 수 검증
                cur.execute("SELECT COUNT(*) FROM chunks WHERE source_id = %s", (sid,))
                db_count = cur.fetchone()[0]
            if db_count != len(chunks):
                conn.rollback()
                mismatches.append((sid, len(chunks), db_count))
                continue
            conn.commit()
            total_chunks += db_count
            loaded_sources += 1
            emb_restored += stats["emb_restored"]
            emb_lost += stats["emb_lost"]
            for key in linked_totals:
                linked_totals[key] += stats[key]
        except Exception as e:
            conn.rollback()
            skipped.append((sid, f"오류: {type(e).__name__} {str(e)[:80]}"))
            continue

        if i % 50 == 0 or i == len(source_ids):
            print(f"  진행 {i}/{len(source_ids)}  (누적 {total_chunks:,}청크)")

    # speakers 집계 재생성
    with conn.cursor() as cur:
        speaker_count = rebuild_speakers(cur)
        cur.execute("SELECT COUNT(*) FROM committees")
        committee_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM meetings")
        meeting_count = cur.fetchone()[0]
    conn.commit()
    conn.close()

    print("\n" + "=" * 56)
    print("  적재 완료 요약")
    print("=" * 56)
    print(f"  committees : {committee_count:>8,}")
    print(f"  meetings   : {meeting_count:>8,}  (적재 source {loaded_sources}/{len(source_ids)})")
    print(f"  speakers   : {speaker_count:>8,}")
    print(f"  chunks     : {total_chunks:>8,}")
    print(f"  embeddings : 재사용 {emb_restored:,} / 신규필요 {emb_lost:,} (내용 바뀐 청크)")
    print(f"  issue map  : 보존 {linked_totals['issue_restored']:,} / 제거 {linked_totals['issue_lost']:,}")
    print(f"  stances    : 보존 {linked_totals['stance_restored']:,} / 무효화 {linked_totals['stance_lost']:,}")
    print(f"  summaries  : 보존 {linked_totals['summary_restored']:,} / 무효화 {linked_totals['summary_lost']:,}")
    if emb_lost:
        print("    → 신규필요분은 scripts/embeddings_v1.py 재실행으로 채우세요 (증분).")
    if skipped:
        print(f"\n  건너뜀 {len(skipped)}건:")
        for sid, reason in skipped[:20]:
            print(f"    - {sid}: {reason}")
        if len(skipped) > 20:
            print(f"    ... 외 {len(skipped) - 20}건")
    if mismatches:
        print(f"\n  [경고] 행 수 불일치 {len(mismatches)}건 (롤백됨):")
        for sid, jsonl_n, db_n in mismatches:
            print(f"    - {sid}: JSONL {jsonl_n} vs DB {db_n}")
    print("=" * 56)
    if skipped or mismatches:
        sys.exit(1)


if __name__ == "__main__":
    main()
