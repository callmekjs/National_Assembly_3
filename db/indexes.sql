-- 검색 인덱스 (schema.sql 과 분리 — 대량 적재·재임베딩 시 DROP → 적재 → 재생성이
-- 유지 상태 삽입보다 빠르기 때문. 새 환경에서는 schema.sql → 데이터 적재 →
-- 임베딩 생성 → 이 파일 순으로 실행한다.)
--
-- 실행: psql "$DATABASE_URL" -f db/indexes.sql
-- 참고 실측 (2026-07-02, 419,882청크):
--   - HNSW 빌드 ~17분. Windows 로컬은 병렬 빌드가 공유 메모리 초과(DiskFull) →
--     SET max_parallel_maintenance_workers = 0; 후 생성할 것
--   - trgm GIN 인덱스 전 374ms → 후 4.6ms (키워드 검색)

-- 한국어 키워드 검색 (RAG-2): pg_trgm 부분 문자열 매칭
-- (FTS 는 조사 붙은 형태 21% 손실 실측으로 기각 — progress.md RAG-2)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_chunks_text_trgm
  ON chunks USING gin (text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_speaker_trgm
  ON chunks USING gin (speaker gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_role_trgm
  ON chunks USING gin (role gin_trgm_ops);

-- 벡터 검색 (RAG-3): HNSW 코사인 (search_vector.py 가 ef_search=100 으로 사용)
CREATE INDEX IF NOT EXISTS idx_embeddings_openai_hnsw
  ON embeddings_openai USING hnsw (embedding vector_cosine_ops);
