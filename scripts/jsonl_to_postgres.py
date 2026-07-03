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


def insert_chunks(cur, chunks: list[dict], committee_id: int | None) -> None:
    source_id = chunks[0]["source_id"]
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
                insert_chunks(cur, chunks, committee_id)
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


if __name__ == "__main__":
    main()
