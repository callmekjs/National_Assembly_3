# 쟁점 모듈 스펙 — 이슈 사전 + 이슈↔청크 매핑 (3단계 POL-3)

> 2026-07-08 설계 확정 (브레인스토밍 — 사용자 결정 3건 반영). 목표: 22대 국회 주요 이슈
> 20~30개를 데이터 근거로 정의하고, 이슈별 관련 발언(청크)을 정밀도 우선으로 매핑해
> POL-4(타임라인)·POL-5(입장)·POL-6(여야 구도)의 공통 기반을 만든다.

## 사용자 결정 (2026-07-08)

1. **이슈 정의 = 사건+정책 혼합** — 사건형(계엄·탄핵·티메프 등)과 정책형(AI 규제·공영방송
   지배구조 등)을 모두 포함. 실사용 질문이 양쪽으로 들어오므로 한쪽만 담으면 사전이 반쪽.
2. **선정 = 코퍼스 탐사 + 사용자 확정** — 데이터에서 후보 40~60개를 근거(발언량·기간·위원회)와
   함께 추출하고, 사용자가 최종 20~30개를 검수·확정. 데이터 있는 이슈만 등재(빈 타임라인 방지),
   도메인 판단은 사람이 보증.
3. **매핑 = 검색 확장 + LLM 관련도 판정 2단** — 하이브리드 검색(재현율) → 저점수 컷 →
   gpt-4o-mini 배치 판정(정밀도) → 통과분만 저장. 2안(임계값 컷 단독)은 1차 필터로 흡수,
   3안(키워드 단독)은 기각 — POL-1에서 같은 패턴(bill_refs·stance_signals)이 실패한 전례.
4. **매핑 등급 2단화 (2026-07-08 추가)** — 시간 층화 수집(v1.2)이 한산한 달의 "스치는 언급"을
   대거 편입시켜 정밀도와 충돌(실측 80.4%). 매핑을 **core(실질 논의) / mention(언급·절차)**
   등급으로 2차 분류해 충돌을 구조로 해소: `issue_chunks.judge` = `llm_core` | `llm_mention`.
   **게이트는 core 등급 정밀도 ≥90%로 개정.** 소비 규칙: POL-4 타임라인은 전체(발언량 신호,
   시간 형태 보존), POL-5 입장·POL-6 구도는 core만(순도 우선). 삭제하지 않고 등급만 부여
   (타임라인 형태 훼손 방지).

## 전제 (기존 실측 기반)

- 이슈-청크 매핑에 **bill_refs 사용 금지** (POL-1 판정: 정밀도·재현율 모두 불가)
- 하이브리드 검색 R@5=0.983 (reranker 채택 후) — 후보 수집기로 신뢰 가능
- LLM listwise 판정 패턴은 reranker(2026-07-07 채택)로 검증됨 — 같은 패턴 재사용
- 신뢰 원칙: 틀린 매핑보다 누락 (정당 라벨 "틀린 라벨보다 무표기"와 동일)

## 산출물

### 1. `data/issues/issues_seed.json` (이슈 사전 원본 — git 보존, 재현성)

이슈당 필드:

| 필드 | 내용 | 용도 |
|------|------|------|
| `issue_id` | 슬러그 (예: `martial-law`, `ai-basic-act`) | PK, URL·API 키 |
| `title` | 표시명 (예: "12·3 비상계엄") | 프론트·답변 표기 |
| `type` | `event` \| `policy` | 후속 분석 분기 (타임라인은 event 강점, 입장은 policy 강점) |
| `description` | 1~2문장 정의 | **LLM 관련도 판정의 기준문** — 경계를 여기서 결정 |
| `seed_keywords` | 키워드·별칭·변칭 배열 (예: "비상계엄", "12·3", "계엄령") | 키워드 후보 수집 |
| `seed_queries` | 검색용 자연어 질문 2~3개 | 하이브리드 후보 수집 |
| `anchor_meetings` | "반드시 잡혀야 하는" 대표 회의 source_id 1~2개 | 재현율 참고 체크 |

### 2. DB 테이블 2개 (`db/schema.sql` 추가)

```sql
-- 이슈 사전 (issues_seed.json 적재본)
CREATE TABLE IF NOT EXISTS issues (
  issue_id    TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  type        TEXT NOT NULL,          -- event | policy
  description TEXT NOT NULL,
  seed        JSONB NOT NULL,         -- keywords/queries/anchors 원본
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- 이슈↔청크 매핑 (build_issue_map.py 가 채운다)
CREATE TABLE IF NOT EXISTS issue_chunks (
  issue_id    TEXT NOT NULL REFERENCES issues(issue_id) ON DELETE CASCADE,
  chunk_id    TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  turn_id     TEXT,                   -- POL-4 집계는 turn 단위 (청크 분할 중복 방지 — actors.py 교훈)
  vec_score   REAL,                   -- 후보 수집 시 벡터 유사도 (디버깅)
  kw_hit      BOOLEAN,                -- 키워드 매치 여부 (디버깅)
  judge       TEXT NOT NULL,          -- 편입 근거 기록: 현재 'llm_relevant' 단일 (판정 방식이 바뀌면 값 추가)
  map_version TEXT NOT NULL,          -- 매핑 방법 버전 (재매핑 추적)
  mapped_at   TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (issue_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_issue_chunks_chunk ON issue_chunks(chunk_id);
```

### 3. 스크립트 3개 (`scripts/`)

| 스크립트 | 역할 |
|----------|------|
| `issue_candidates.py` | 코퍼스 탐사 → 이슈 후보 40~60개 리포트 (사용자 검수용) |
| `build_issue_map.py` | issues_seed.json → 검색 확장 → 컷 → LLM 판정 → issues·issue_chunks 적재 |
| `issue_spotcheck.py` | 매핑 무작위 표본 추출 (판독용) + 앵커 회의 포함 확인 |

### 4. API — `GET /issues` 하나만

목록(issue_id, title, type, description, 매핑 청크 수·turn 수). 타임라인·상세는 POL-4 몫 (YAGNI).

## 단계 1 — 이슈 후보 탐사 (`issue_candidates.py`)

네 가지 데이터 신호를 교차해 후보 40~60개 + 근거 리포트 생성:

1. **시계열 스파이크**: 위원회×월별 발언량(turn 수) 집계 → 평시 대비 급증 구간 → 사건형 후보.
   *자기 검증: 계엄(2024-12)·탄핵 국면이 안 잡히면 신호 로직이 틀린 것.*
2. **agenda 신호**: normalized 데이터의 안건(agenda) 섹션 텍스트에서 빈발 의제 → 정책형 후보
3. **LLM 표본 요약**: 위원회×분기별 대표 청크 표본(예: 셀당 30청크)을 gpt-4o-mini로 요약해
   반복 주제 추출 — 전량 클러스터링 기각(42만 청크 품질 통제 불가), 표본 요약은 수십 센트
4. **query_logs 교차**: 실사용 질문과 후보의 겹침 표시 — 실수요 근거

리포트 형식: 후보별 (가칭, 추정 발언 규모, 주요 위원회, 기간, 신호 출처) → **사용자가 20~30개
확정 + seed_keywords/queries/anchor 보강 → issues_seed.json 작성** (이 확정 단계는 사람 작업).

## 단계 2 — 매핑 파이프라인 (`build_issue_map.py`)

이슈별 처리 (이슈 단위 트랜잭션):

1. **후보 수집 (재현율)**: seed_queries 각각 하이브리드 검색(축별 상한 넉넉히, 예: 300) +
   seed_keywords pg_trgm 검색 → 합집합, chunk_id 기준 dedup
2. **저점수 컷 (1차 필터, 비용 절감)**: 벡터 유사도 < `GROUNDING_SIM_THRESHOLD`(0.4) **이고**
   키워드 매치 0 → 제외 (grounding 사전차단 로직 재사용 — 새 임계값 발명 금지)
3. **LLM 관련도 판정 (정밀도)**: gpt-4o-mini, 청크 20개 배치.
   프롬프트: issue의 title+description 을 기준문으로, 청크 발췌(예: 600자)를 보여주고
   관련/무관 이진 판정. **"단어만 스치듯 언급하는 발언·의사진행 발언은 무관"** 기준 명시.
   출력은 구조화(번호 목록) — 파싱 실패 배치는 재시도 1회 후 해당 배치 전체 제외(누락 우선)
4. **적재**: 통과분만 issue_chunks에 이슈 단위 DELETE+재삽입 (jsonl_to_postgres 패턴),
   map_version 기록, 적재 후 행수 검증

운영 옵션: `--dry-run`(후보 수·예상 비용만), `--issue <id>`(단일 이슈 재실행 — 시드 수정 시),
일시 오류(RateLimit/Timeout/5xx)만 지수 백오프 재시도 (embeddings_v1 패턴), 실패 이슈는
기록 후 exit 1 (조용한 유실 금지).

예상 비용: 이슈 30개 × 후보 1,000~2,000 × 판정 입력 ~600자 → **$2~4 일회성** (실행 전
--dry-run 으로 확정).

## 단계 3 — 검증 (완료 기준)

1. **정밀도 스팟체크**: 이슈당 무작위 10청크(seed 고정, 재현 가능) 원문 대조 판독 —
   **이슈 평균 정밀도 ≥90% 게이트** (enrichment_audit 패턴). 미달 이슈는 description/시드
   보정 후 `--issue` 재실행
2. **재현율 참고 체크**: anchor_meetings 의 청크가 매핑에 포함되는지 이슈별 확인 —
   게이트는 아니고(재현율 전수 측정은 불가능) 경보 신호
3. **단위 테스트** (`tests/test_issue_map.py` — LLM·DB 없는 순수 로직):
   판정 응답 파싱(정상·부분·실패), 저점수 컷 경계, chunk dedup, 배치 분할
4. **GET /issues 스모크**: 이슈 수·청크 수 정합 (issue_chunks 집계와 일치)

## 범위 제외 (명시)

- 신규 회의록 인입 시 자동 재매핑 — 증분 인입(7순위) 구현 시 `--issue` 전체 재실행으로 대응
- 이슈 간 계층·관계 모델링 — POL-4~6에 불필요
- 프론트 이슈 대시보드 — POL-9
- 이슈별 답변(/query) 연동 — POL-8 (분석 통합)에서 결정

## 알려진 한계 (문서화)

- **core 게이트 최종 86.2% — 명목 기준(≥90%) 미달 마감 (2026-07-09 사용자 결정)**: 17/24 이슈는
  ≥90%. LLM 판독 기준이 보정 라운드마다 엄격해지는 측정 표류로 수렴 불가 판단 — 상세·미달 7개
  목록·POL-5/6 소비 지침은 spotcheck_report.md 최종 판정 요약과 progress.md "POL-3 마감" 참조.
  인접 이슈 간 정의 경계(특히 방송 3이슈)가 본질적 모호 — POL-5 착수 시 재검토
- **매핑은 스냅샷**: 코퍼스가 2026-06-30 까지 — 이후 데이터는 재매핑 필요 (map_version 으로 추적)
- **재현율은 미측정**: 정밀도만 게이트. 시드에 없는 표현으로만 언급된 발언은 누락 가능
  (신뢰 원칙상 의도된 트레이드오프 — 누락 > 오염)
- **LLM 판정 비결정성**: 같은 청크가 재실행 시 다르게 판정될 수 있음 — map_version 과
  판정 저장으로 변화 추적, 경계 사례는 스팟체크에서 관찰

## 구현 순서 (계획 문서에서 상세화)

1. issue_candidates.py + 후보 리포트 → **사용자 확정 (사람 게이트)**
2. issues_seed.json 작성 + schema.sql 테이블 추가
3. build_issue_map.py (+ 단위 테스트) → --dry-run 비용 확인 → 실행
4. issue_spotcheck.py → 정밀도 게이트 → 미달 이슈 보정 루프
5. GET /issues + 스모크 → progress.md 기록
