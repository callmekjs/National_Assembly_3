-- 국회 회의록 RAG 서비스 — PostgreSQL 스키마 (v1)
--
-- 실행: national_assembly 데이터베이스에 접속한 뒤 전체 실행
--   psql "$DATABASE_URL" -f db/schema.sql
-- 또는 jsonl_to_postgres.py 가 시작 시 자동 실행한다 (IF NOT EXISTS 라 반복 안전).
--
-- 설계 원칙 (마스터 설계 문서 3-4, 4-5 반영):
--   - 정규화: committees / meetings / speakers / chunks / embeddings_openai
--   - 임베딩은 별도 테이블, 이름에 모델명 반영 → 모델 교체 시 chunks 안 깨짐
--   - chunk_id 는 안정적으로 유지 (출처 원문 조회 기준)
--   - chunks 에 committee_id / meeting_date 는 검색 속도용 의도적 비정규화

CREATE EXTENSION IF NOT EXISTS vector;

-- 1. 위원회 (9행)
CREATE TABLE IF NOT EXISTS committees (
  committee_id  SERIAL PRIMARY KEY,
  name          TEXT UNIQUE NOT NULL,   -- 약칭: 과방위
  full_name     TEXT,                   -- 정식명칭: 과학기술정보방송통신위원회
  policy_domain TEXT                    -- 정책분야: 과학기술/방송통신/ICT
);

-- 2. 회의 (PDF 1개 = 회의 1개)
CREATE TABLE IF NOT EXISTS meetings (
  source_id     TEXT PRIMARY KEY,       -- 과방위_20240611_52074_52074
  committee_id  INT REFERENCES committees(committee_id),
  file_name     TEXT,
  meeting_date  DATE
);

-- 3. 발언자 (chunks 에서 집계로 유도되는 조회·분석용 테이블)
CREATE TABLE IF NOT EXISTS speakers (
  speaker_id      SERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  role            TEXT,
  committee_id    INT REFERENCES committees(committee_id),
  utterance_count INT DEFAULT 0,
  UNIQUE (name, role, committee_id)
);

-- 4. 청크 (검색·인용 단위)
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id        TEXT PRIMARY KEY,
  turn_id         TEXT,
  chunk_type      TEXT,
  chunk_index     INT,
  chunk_total     INT,
  source_id       TEXT NOT NULL REFERENCES meetings(source_id) ON DELETE CASCADE,
  committee_id    INT REFERENCES committees(committee_id),  -- 검색 필터용 (의도적 중복)
  meeting_date    DATE,                                     -- 검색 필터용 (의도적 중복)
  speaker         TEXT,
  role            TEXT,
  page_start      INT,
  page_end        INT,
  text            TEXT NOT NULL,
  context_before  TEXT,
  context_after   TEXT,
  embed_text      TEXT,
  is_short        BOOLEAN,
  policy_domain   TEXT,
  bill_refs       JSONB,
  utterance_type  TEXT,
  stance_signals  TEXT,
  mentions        JSONB,
  parser_version  TEXT,
  chunker_version TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);

-- 5. 임베딩 (OpenAI text-embedding-3-small = 1536차원). ETL-8 에서 채운다.
CREATE TABLE IF NOT EXISTS embeddings_openai (
  chunk_id    TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  embedding   vector(1536),
  model       TEXT NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- 인덱스 (필터·조인 대상 컬럼)
CREATE INDEX IF NOT EXISTS idx_meetings_committee_id ON meetings(committee_id);
CREATE INDEX IF NOT EXISTS idx_meetings_meeting_date ON meetings(meeting_date);
CREATE INDEX IF NOT EXISTS idx_speakers_committee_id ON speakers(committee_id);
CREATE INDEX IF NOT EXISTS idx_speakers_name         ON speakers(name);
CREATE INDEX IF NOT EXISTS idx_chunks_source_id      ON chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_chunks_committee_id   ON chunks(committee_id);
CREATE INDEX IF NOT EXISTS idx_chunks_meeting_date   ON chunks(meeting_date);
CREATE INDEX IF NOT EXISTS idx_chunks_speaker        ON chunks(speaker);

-- 벡터 검색 인덱스(HNSW)와 한국어 키워드 검색 인덱스(pg_trgm)는
-- db/indexes.sql 로 생성한다 (대량 적재 시 DROP→적재→재생성 운용을 위해 분리).

-- 6. 질의 로그 (RAG-7). /query 호출마다 1행 — 답변·신뢰등급·비용 기록,
--    /feedback 이 rating 컬럼을 UPDATE 한다 (질문당 피드백 1개 — 별도 테이블은 YAGNI).
CREATE TABLE IF NOT EXISTS query_logs (
  query_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question   TEXT NOT NULL,
  mode       TEXT NOT NULL,                    -- qa | report
  committee  TEXT,
  date_from  DATE,
  date_to    DATE,
  answer     TEXT NOT NULL,
  grounding  TEXT NOT NULL,                    -- FULL | PARTIAL | REFUSED | NONE
  citations  JSONB NOT NULL DEFAULT '[]',
  invalid_citations JSONB NOT NULL DEFAULT '[]',
  usage      JSONB,                            -- 토큰·비용 (사전차단 시 NULL)
  latency_ms INT,
  source_block TEXT,                           -- LLM 에 실제로 들어간 근거 블록 (디버깅 재현용, 사전차단 시 NULL)
  created_at TIMESTAMPTZ DEFAULT now(),
  rating     INT,                              -- /feedback
  feedback_comment TEXT,
  feedback_at TIMESTAMPTZ
);

-- 기존 DB 마이그레이션 (CREATE IF NOT EXISTS 는 기존 테이블에 컬럼을 못 더한다)
ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS source_block TEXT;

CREATE INDEX IF NOT EXISTS idx_query_logs_created_at ON query_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_query_logs_grounding  ON query_logs(grounding);

-- 7. 22대 의원-정당 매핑 (정당 모듈, 3단계 행위자 분석). scripts/build_members.py 가 채운다.
--    party 는 위성정당 정규화된 최종 당적 (스냅샷 — 임기 중 탈당 추적 불가, spec 참조)
CREATE TABLE IF NOT EXISTS members (
  member_id  TEXT PRIMARY KEY,       -- 열린국회정보 NAAS_CD
  name       TEXT NOT NULL,
  hanja_name TEXT,
  party      TEXT NOT NULL,
  party_raw  TEXT,                   -- PLPT_NM 원본 (커리어 이력)
  era        TEXT,
  committees TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_members_name ON members(name);

-- 8. 쟁점 사전 (POL-3). data/issues/issues_seed.json 을 build_issue_map.py 가 적재한다.
CREATE TABLE IF NOT EXISTS issues (
  issue_id    TEXT PRIMARY KEY,       -- 슬러그: martial-law, ai-basic-act
  title       TEXT NOT NULL,          -- 표시명: 12·3 비상계엄
  type        TEXT NOT NULL,          -- event | policy
  description TEXT NOT NULL,          -- LLM 관련도 판정의 기준문
  seed        JSONB NOT NULL,         -- keywords/queries/anchor_meetings 원본
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- 9. 이슈↔청크 매핑 (POL-3). 검색 확장 + LLM 판정 통과분만 저장 (누락 > 오염).
--    turn_id 동시 저장: POL-4 집계는 turn 단위 (청크 분할 중복 방지 — actors.py 교훈)
CREATE TABLE IF NOT EXISTS issue_chunks (
  issue_id    TEXT NOT NULL REFERENCES issues(issue_id) ON DELETE CASCADE,
  chunk_id    TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  turn_id     TEXT,
  vec_score   REAL,                   -- 후보 수집 시 벡터 유사도 (키워드 단독 편입이면 NULL)
  kw_hit      BOOLEAN,                -- 키워드 매치 여부 (디버깅)
  judge       TEXT NOT NULL,          -- 편입 근거: 현재 'llm_relevant' 단일
  map_version TEXT NOT NULL,          -- 매핑 방법 버전 (재매핑 추적)
  mapped_at   TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (issue_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_issue_chunks_chunk ON issue_chunks(chunk_id);

-- 10. 이슈↔행위자 입장 (POL-5). core turn 을 LLM 5택 판정 (support|oppose|concern|neutral|none).
CREATE TABLE IF NOT EXISTS issue_stances (
  issue_id    TEXT NOT NULL REFERENCES issues(issue_id) ON DELETE CASCADE,
  turn_id     TEXT NOT NULL,
  speaker     TEXT,
  role        TEXT,
  stance      TEXT NOT NULL,   -- support | oppose | concern | neutral | none
  judge_model TEXT NOT NULL,
  map_version TEXT NOT NULL,
  mapped_at   TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (issue_id, turn_id)
);

-- 11. 최근 발언 한 줄 요약 캐시 (의원 프로필). 코퍼스가 정적이라 영구 유효 —
--     backend/utterance_summary.py 가 첫 조회 시 자가 생성(CREATE IF NOT EXISTS)하므로
--     이 정의는 문서화 목적. 배포 DB 에도 첫 사용 시 자동 생성된다.
CREATE TABLE IF NOT EXISTS utterance_summaries (
  chunk_id   TEXT PRIMARY KEY,
  summary    TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
