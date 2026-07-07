# 국회 RAG 서비스 — 개발 진행 현황

최종 업데이트: 2026-07-07

---

## 전체 마일스톤 상태

| 단계 | 내용 | 상태 |
|------|------|------|
| 1단계 | 프로젝트 뼈대 + FastAPI 스텁 + React UI | ✅ 완료 |
| ETL-0 | 9개 위원회 PDF 수집 (767개) | ✅ 완료 |
| ETL-1 | PDF 텍스트 추출 (extractor_v1) | ✅ 완료 |
| ETL-2 | 텍스트 정규화 (normalizer_v1) | ✅ 완료 |
| ETL-3 | 발언자 턴 파싱 (parser_v1) | ✅ 완료 |
| ETL-4 | turns quality gate (767/767 PASS) | ✅ 완료 |
| ETL-5 | 정책 도메인 enrichment (policy_enricher_v1) | ✅ 완료 |
| ETL-6 | RAG 청크 생성 (chunker_v1) | ✅ 완료 |
| ETL-7 | PostgreSQL 적재 (jsonl_to_postgres) | ✅ 완료 |
| ETL-8 | 임베딩 생성 (embeddings_v1) | ✅ 완료 |
| 2단계 | 최소 검색형 RAG (DB + 검색 + 답변 + 출처) | ✅ 완료 (2026-07-03, RAG-0~8 + 완료 기준 4/4) |
| 3단계 | 정책 도메인 분석 기능 | 🔶 진행 중 (1차 정당 모듈 ✅ 2026-07-03) |
| 4단계 | GovTech 배포 버전 | ⬜ 예정 |

---

## ETL 파이프라인 최종 결과 (2026-07-01)

| 단계 | source | 산출물 | 오류 |
|------|--------|--------|------|
| extract | 767개 | 41,571페이지 | 0 |
| normalize | 767개 | 41,571페이지 | 0 |
| parse | 767개 | 418,758턴 | 0 |
| turns_quality_gate | 767개 | 767 PASS / 0 BLOCK | — |
| enrich | 767개 | 418,758턴 | 0 |
| chunk | 767개 | 419,882청크 | 0 |

---

## ETL-0 — PDF 수집

| 위원회 | 폴더 | PDF 수 |
|--------|------|--------|
| 과학기술정보방송통신위원회 | 과방위 | 161 |
| 행정안전위원회 | 행안위 | 121 |
| 국토교통위원회 | 국토위 | 100 |
| 정무위원회 | 정무위 | 87 |
| 보건복지위원회 | 복지위 | 85 |
| 산업통상자원중소벤처기업위원회 | 산자중기위 | 78 |
| 국방위원회 | 국방위 | 57 |
| 외교통일위원회 | 외통위 | 56 |
| 재정경제기획위원회 | 기재위 | 22 |
| **합계** | | **767** |

- 수집 기간: 2024-05-30 ~ 2026-06-30 (제22대 국회 전체)
- CSRF 처리, `committeeCd` 기반 위원회 필터, `resultCnt` 기반 페이지네이션

---

## ETL-1 — PDF 텍스트 추출 (extractor_v1)

- **입력**: `incoming_data/{위원회}/*.pdf` 767개
- **출력**: `data/v1/extract/{source_id}/pages.jsonl`
- **라이브러리**: pdfplumber
- **멱등성**: `pages.jsonl` 존재 시 스킵

---

## ETL-2 — 텍스트 정규화 (normalizer_v1)

- **출력**: `data/v1/normalized/{source_id}/normalized.jsonl`
- **처리 내용**:
  - 반복 헤더/페이지번호 제거
  - 한국어 줄바꿈 복원 (형태소 기반 join/split)
  - `section_type` 분류: `cover` / `agenda` / `body` / `report` / `mixed`
  - `segments` 배열 생성 (페이지 내 구간 분리)
  - `has_speaker_marker` 계산 (body 세그먼트 기준)
- **버전**: NORMALIZER_VERSION = v1.0

---

## ETL-3 — 발언자 턴 파싱 (parser_v1)

- **출력**: `data/v1/parsed/{source_id}/turns.jsonl`
- **마커**: `◯` (전 위원회 공통) + `◎` (정무위 일부)
- **처리 내용**:
  - body 세그먼트만 파싱
  - 페이지 경계 continuation 처리 (cover 세그먼트 연속 발언 이어붙임)
  - 비발언 항목 제외: 출석 위원, 의안 회부, 보고사항 등
  - `page_start` / `page_end` 추적
  - `meeting_date` YYYY-MM-DD 정규화
- **버전**: PARSER_VERSION = v1.2

### 발언자 추출 패턴

| 패턴 | 예시 |
|------|------|
| 역할 선행 | `◯위원장 최민희` → role=위원장, name=최민희 |
| 역할 선행(일반형, v1.1~v1.2) | `◯증인 유영상`, `◯경찰청장 조지호`, `◯한국방송공사사장후보자 박장범`, `◯보건복지부제2차관 이형훈` |
| 이름 선행 | `◯김현 위원`, `◯柳榮夏 위원`(한자) → role=위원 |
| 부처+직책 | `◯외교부장관 조태열` → role=외교부장관, name=조태열 |

- 이름부: 한국명(2~7자), 외국인 음차명(`해럴드로저스`), 한자명(`柳榮夏` — 호환용 한자 U+F900 블록 포함), 익명처리(`박00`)
- 접두부: 조직명에 숫자(`제2차관`)·특수문자(`쿠팡㈜`, `(전)`) 허용
- **버려진 헤더 리포트**: 발언자 추출 실패 헤더를 `data/v1/reports/parser_dropped_headers.txt` 에 기록 — "조용한 폐기" 방지 장치 (v1.2)

### 중간 발견 버그 및 수정

| 버그 | 원인 | 수정 |
|------|------|------|
| `speaker="의안"` BLOCK 20건 | `_NON_SPEAKER_NAMES`에 `"의안"` 누락 | `"의안"` 추가 후 20개 재파싱 |
| 직함이 이름으로 저장 31,258청크 (9.5%) | `증인/참고인/전문위원/청장·차장·위원장+이름` 역할 선행 패턴 미인식 → 첫 단어를 이름으로 오인, 실명은 본문으로 유실 | v1.1: `ROLE_FIRST_GENERAL_RE` 일반화 정규식 추가 → 잔존 2청크(0.0006%) |
| v1.0이 발언 27,091턴 무단 폐기 | `수석전문위원`(6자+) 등 어떤 패턴에도 안 걸리는 헤더는 턴 자체를 drop | v1.1 일반화 패턴이 흡수 → 418,758턴으로 복구 |
| `출장/위원/소위원회` 등 비발언 항목이 발언자로 등록 | 출석 통계·의안 회부 헤더 미차단 | `_NON_SPEAKER_HDR`/`_NON_SPEAKER_NAMES` 보강 |
| chunks_quality_gate `--all`이 아무것도 검사 안 함 | 경로가 `data/v2/chunks`로 오타 → "검사할 파일 없음" exit 0 | `data/v1/chunks`로 수정, 767/767 실검사 통과 확인 |
| `청가`(위원 휴가 신고)가 발언자로 등록 425청크 | 원본 대조 감사(etl_audit)가 무작위 샘플에서 발견 | v1.2: 비발언 헤더 차단 추가 |
| **후보자·직무대행·제N차관 등 59,550건 조용히 폐기** | `후보자`/`직무대행`/`사령관`/`총재` 등 접미사 미지원 + 직함 내 숫자(`제2차관`)·특수문자(`쿠팡㈜`)·한자명(`柳榮夏`) 미지원. 인사청문회 후보자(이진숙 3,716턴, 박장범 2,379턴), 계엄 국면 직무대행(김선호 2,153턴, 이호영 1,939턴) 발언이 통째로 누락돼 있었음 | v1.2: 버려진 헤더 5.9만 건을 유형별 집계 → 접미사 40여 종 데이터 기반 확장 + 숫자/특수문자/한자(호환 블록 포함) 허용. 버려진 헤더 59,550→1,001건(-98%, 잔여는 참석자 명단 등 정당한 제외) |

- **단위 테스트**: `tests/test_parser_speaker.py` (실측 헤더 50케이스) + `tests/test_quality_gates.py` (게이트 자체 검증 4케이스 — 무검사 통과 재발 방지)
- **v1.0→v1.2 누적 효과**: 발언자 오류율 9.5% → 0.0006%, 유실 발언 62,000+턴 복구 (326,356 → 418,758턴)
- **원본 대조 감사**: `scripts/etl_audit.py` — 무작위 300청크를 원본 추출 텍스트와 대조 (본문·발언자·페이지). **최종 300/300 (100%) 통과**

---

## ETL-4 — Turns Quality Gate

- **스크립트**: `scripts/turns_quality_gate.py`
- **결과**: 767/767 PASS, BLOCK 0건
- **검사 항목 (BLOCK)**:
  - speaker 누락률 1% 이상
  - meeting_date 형식 오류
  - turn_id 중복
  - page_start > page_end
  - 비발언 항목 speaker 잔존
  - 필수 메타데이터 누락
- **검사 항목 (WARNING)**:
  - ◯ 마커 text 잔존
  - 20자 미만 turn 비율 20% 이상 (다수 source에서 경고 발생 — 짧은 의사진행 발언 특성)
- **리포트 위치**: `data/v1/reports/turns_quality/`

---

## ETL-5 — 정책 도메인 Enrichment (policy_enricher_v1)

- **출력**: `data/v1/enriched/{source_id}/enriched_turns.jsonl`
- **추가 필드**:

| 필드 | 방식 | 예시 |
|------|------|------|
| `policy_domain` | 위원회명 → 정책분야 매핑 | 과방위 → "과학기술/방송통신/ICT" |
| `bill_refs` | 법안명 패턴 매칭 | ["방송법 개정안", "AI기본법"] |
| `utterance_type` | 질의/발의/진술 분류 | "question" / "statement" / "motion" |
| `stance_signals` | 찬반 키워드 탐지 | "positive" / "negative" / "neutral" / "mixed" |
| `mentions` | 부처·기관명 추출 | ["과학기술정보통신부", "방통위"] |

---

## ETL-6 — RAG 청크 생성 (chunker_v1)

- **출력**: `data/v1/chunks/{source_id}/chunks_v1.jsonl`
- **분할 규칙**:
  - 2,500자 초과 → 문장 단위 분할
  - 그 외 → turn 1개 = chunk 1개
- **핵심 필드**:
  - `embed_text`: `{위원회} {날짜} {발언자} {역할} 발언: {본문}` 형식
  - `context_before` / `context_after`: 앞뒤 turn 80자
  - `is_short`: 150자 미만 플래그
  - policy enrichment 필드 전부 보존

---

## ETL-7 — PostgreSQL 적재 (jsonl_to_postgres_v1)

- **스키마 정의**: `db/schema.sql` (정규화 5테이블 + pgvector 확장)
- **DB**: `national_assembly` (localhost:5432, PostgreSQL + pgvector 0.8.1)
- **호스팅 (2026-07-03 이름 정리)**: Docker 컨테이너 **`national-assembly-db`**, 볼륨
  **`national_assembly_pgdata`**, restart unless-stopped — Docker Desktop이 꺼져 있으면 먼저 실행.
  구 `1st_Project_upgrade`/`skn18-1st-4team_postgres_data`에서 볼륨 복사로 마이그레이션 후 구본 삭제.
  구 프로젝트 DB skn_project(2.2GB)는 2026-07-03 DROP — 볼륨엔 national_assembly + test(57MB)만 남음
- **테이블 구조** (마스터 설계 문서 3-4 반영):

| 테이블 | 행 수 | 설명 |
|--------|-------|------|
| committees | 9 | 위원회 (name 약칭 / full_name 정식명 / policy_domain) |
| meetings | 767 | 회의 (PDF 1개 = 회의 1개, source_id PK) |
| speakers | 2,292 | 발언자 (chunks 집계로 유도, utterance_count) |
| chunks | 419,882 | 검색·인용 단위 (meetings·committees FK) |
| embeddings_openai | 419,882 | 임베딩 (text-embedding-3-small, vector(1536), HNSW 인덱스) |

- **적재 방식**:
  - source(회의)별 committees/meetings upsert → committee_id 확보
  - chunks 는 `source_id` 기준 DELETE 후 재삽입 (재실행 안전, `execute_values` 대량 삽입)
  - 적재 직후 행 수 검증 (JSONL 줄 수 == DB 행 수, 불일치 시 롤백)
  - 인라인 품질 체크 (meeting_date/빈 텍스트 비율 초과 source skip)
  - 종료 후 speakers 를 chunks 에서 집계 재생성
- **결과**: 767/767 source 적재, 419,882청크, 고아 청크 0건, 건너뜀·불일치 0건
- **의도적 비정규화**: chunks 에 committee_id/meeting_date 중복 저장 → 벡터 검색 필터 속도 최적화

---

## ETL-8 — OpenAI 임베딩 생성 (embeddings_v1)

- **모델**: text-embedding-3-small (1536차원)
- **대상**: chunks.embed_text 419,882개 (6,310만 자)
- **실행 결과** (2026-07-02): 419,882/419,882 완료 (파서 v1.2 재처리 후 재임베딩), 실비용 약 $1.26
  - 참고: v1.1 데이터로 1차 임베딩($1.1) 후 파서 결함 발견 → v1.2 재처리로 청크 ID가 재배열되어 전량 재임베딩. 순번 기반 chunk_id 의 한계 — v1.3 개선 항목 참조. **프로젝트 임베딩 총비용 ~$2.4**
  - 재임베딩 절차: HNSW DROP → 재적재(CASCADE로 옛 임베딩 자동 삭제) → `--limit 1000` 테스트 → 전체 실행 → HNSW 재생성 (인수인계 절차서: `claude.txt`)
- **처리 방식**:
  - 증분 처리: `embeddings_openai` 에 이미 있는 chunk_id 는 스킵 → 중단 후 재실행 안전
  - 배치 구성: 요청당 최대 800개 텍스트 / 12만 자 (API 토큰 한도 안전선)
  - rate limit·일시 오류는 지수 백오프 재시도 (2→4→8→…초)
  - 배치 단위 커밋 → 실패 시 해당 배치만 롤백
  - `--dry-run` 으로 대상 수·예상 비용 사전 확인 가능
- **HNSW 인덱스**: `idx_embeddings_openai_hnsw` (vector_cosine_ops)
  - Windows 로컬에서 병렬 빌드가 공유 메모리 초과(DiskFull) → `max_parallel_maintenance_workers=0` 으로 해결
  - 대량 재적재 시에는 **인덱스 DROP → 임베딩 → 재생성**이 빠름 (인덱스 유지 상태의 삽입은 느려짐)
- **검색 검증 1 — 주제형 vs 고유명사형** (v1.1 데이터에서 실측):
  - ✅ 주제형 질문("공영방송 지배구조 개선") → 방통위부위원장 발언 등 정확히 검색
  - ⚠️ 고유명사형 질문("AI 기본법", "티메프") → 벡터 단독으로는 부정확.
    키워드로는 각각 229청크·528청크 존재 확인 → **하이브리드 검색(벡터+키워드) 필요성 실측 검증**
    (마스터 설계 문서 3-5 "하이브리드 검색" 원칙의 근거 데이터, 2단계에서 구현)
- **검색 검증 2 — v1.2 복구 발언** (최종 데이터, 16~64ms):
  - ✅ "이진숙 방통위원장 후보자 청문회 발언" → 상위 3건 전부 이진숙/방송통신위원장후보자 (2024-07 청문회)
  - ✅ "경찰청장 직무대행의 계엄 관련 발언" → 박현수 서울경찰청장직무대리 계엄 당일 증언
  - ✅ 한자명 발언자(柳榮夏 위원) 검색 결과 등장 확인
  - ⚠️ 이름 중심 질문(김선호·박대준)은 벡터 단독 랭킹 약함 — 단 SQL 확인 결과 데이터는 완전
    (김선호 2,153청크 — 계엄 3개월 전 "계엄은 행안부장관이나 국방부장관이 건의" 발언 포함, 박대준 821청크)
    → 검증 1과 동일 결론: 하이브리드 검색 필요
- **알려진 한계**: 익명 참고인 `000`(과방위 2025-12-31, p.79) 2청크가 speaker="참고인"으로 저장됨
  — 원본 PDF 자체가 이름을 000으로 익명화한 특수 표기 (사용자가 원본 육안 확인 완료). v1.3에서 name=000/role=참고인로 교정 예정

### 설계 결정 — 한자 이름 발언자 처리 (2026-07-02 확정)

- **현황**: 전체 419,882청크 전수 스캔 결과 한자 이름 발언자는 정확히 2명 —
  `柳榮夏`(유영하, 2,219청크), `李憲昇`(이헌승, 706청크). 공식 회의록 원본이 한자 표기를 쓰는 의원들.
  한글 중복 표기 없음(같은 인물이 두 이름으로 쪼개지는 문제 없음)
- **결정**: 데이터는 **원문 그대로(한자) 보존**, 한글 검색은 **2단계 검색 레이어의 별칭 사전**으로 처리
  (`유영하 ↔ 柳榮夏`, `이헌승 ↔ 李憲昇`)
- **근거**:
  1. 원문 충실 원칙 (GovTech 신뢰 설계 — 출처 확인 시 원본과 표기 일치)
  2. 데이터 정규화(한자→한글 변환)는 embed_text 가 바뀌어 재임베딩 유발 — 별칭 사전은 비용 0
  3. 마스터 설계 문서 9-3 "기관 별칭 처리"(과기정통부↔과학기술정보통신부)와 동일 메커니즘 — 인물 별칭으로 확장
- **대안 기각**: 데이터 자체를 한글로 정규화하는 방안은 v1.3(해시 chunk_id 이후)에서 ~2,925청크만
  재임베딩(~2센트)으로 가능하나, 원문 표기 불일치 트레이드오프가 있어 별칭 사전 우선

---

## 스크립트 목록

| 스크립트 | 역할 | 상태 |
|----------|------|------|
| `crawl_pdfs.py` | 국회 포털 PDF 크롤링 | ✅ |
| `manifest_builder.py` | PDF 목록 스캔 + 해시 기록 | ✅ |
| `extractor_v1.py` | PDF → 페이지 텍스트 | ✅ |
| `normalizer_v1.py` | 잡음 제거 + 섹션 분류 | ✅ |
| `parser_v1.py` | 발언자 턴 구조화 | ✅ |
| `turns_quality_gate.py` | turns 품질 검사 | ✅ |
| `policy_enricher_v1.py` | 정책 도메인 메타데이터 추가 | ✅ |
| `chunker_v1.py` | RAG 청크 생성 | ✅ |
| `chunks_quality_gate.py` | chunks 품질 검사 | ✅ |
| `pipeline_report.py` | 전 단계 현황 집계 | ✅ |
| `jsonl_to_postgres.py` | PostgreSQL 적재 | ✅ |
| `embeddings_v1.py` | OpenAI 임베딩 생성 (--dry-run/--limit) | ✅ |
| `run_pipeline.py` | 파일 파이프라인 순차 실행기 (게이트 실패 시 중단) | ✅ |
| `etl_audit.py` | 무작위 청크 ↔ 원본 대조 감사 | ✅ |
| `retrieval_eval.py` | 검색 품질 평가 (63문항 13유형, keyword/vector/hybrid) | OK |

---

## 데이터 디렉토리 구조

```
National_Assembly_3/
├── incoming_data/              ← PDF 원본 (767개)
│   ├── 과방위/  (161개)
│   ├── 행안위/  (121개)
│   ├── 국토위/  (100개)
│   ├── 정무위/  (87개)
│   ├── 복지위/  (85개)
│   ├── 산자중기위/ (78개)
│   ├── 국방위/  (57개)
│   ├── 외통위/  (56개)
│   └── 기재위/  (22개)
│
└── data/v1/
    ├── extract/               ← pages.jsonl (767개, 41,571페이지)
    ├── normalized/            ← normalized.jsonl (767개)
    ├── parsed/                ← turns.jsonl (767개, 418,758턴)
    ├── enriched/              ← enriched_turns.jsonl (767개)
    ├── chunks/                ← chunks_v1.jsonl (767개, 419,882청크)
    └── reports/
        ├── turns_quality/     ← 767개 source별 quality report
        └── pipeline_report_*.json
```

---

## 2단계 세부 로드맵 (RAG-0 ~ RAG-8)

> 마스터 문서 2단계("질문하면 관련 회의록을 찾고 출처와 함께 기본 답변 제공") 를
> ETL-0~8 방식으로 세분화. ETL-8 검증에서 확보한 실측 근거(하이브리드 필수, 별칭 사전) 반영.

| # | 이름 | 내용 | 완료 기준 | 상태 |
|---|------|------|----------|------|
| RAG-0 | 백엔드 기반 정비 | DB 연결 모듈(.env), connection pool(작게 시작 — 마스터 6-4), `/health` DB 연결·행수 확인 | `/health` → db ok + chunks 419,882 | ✅ |
| RAG-1 | 조회 API 4종 | `/committees` `/meetings` `/speakers` `/citations/{chunk_id}` — 원문 확인 포함 | 4개 엔드포인트 JSON 응답 | ✅ |
| RAG-2 | 키워드 검색 | 한국어 검색 방식 결정(pg_trgm vs FTS — 결정 지점), OR 토큰(마스터 3-5), **별칭 사전**(기관 9-3 + 인물 `유영하↔柳榮夏`·`이헌승↔李憲昇`), 인덱스 생성 | "티메프"·"AI 기본법" 검색 정확 | ✅ |
| RAG-3 | 벡터 검색 | 질문 임베딩 → HNSW 유사도 검색, 필터(위원회/기간/발언자) | 주제형 질문 상위 N 정확 | ✅ |
| RAG-4 | 하이브리드 결합 | RRF 등 순위 융합(결정 지점), is_short 페널티, 동일 turn 중복 제거 | 고유명사형+주제형 모두 정확 | ✅ |
| RAG-5 | 검색 평가 | `retrieval_eval.py` — 질문 15~20개 정답셋, Recall@k·MRR 기준선 (마스터 4-11 "eval은 초반부터") | 기준선 점수 기록, 이후 변경마다 실행 | ✅ |
| RAG-6 | 답변 생성 | GPT-4o-mini + 출처 번호 `[1][2]` 인용 프롬프트, 한국어 규칙, 근거 없는 내용 금지, **qa/report 모드 차등** | 답변의 모든 주장에 출처 연결 | ✅ |
| RAG-7 | `/query` 통합 | 질문→하이브리드→답변→출처 + **Grounding 기초 판정**(FULL/PARTIAL/REFUSED/NONE — 마스터 4-9) + query_logs 저장 | curl 한 번에 답변+출처+신뢰등급 | ✅ |
| RAG-8 | 프론트 연결 | React UI: 질문 입력→답변(Markdown)→출처 패널→원문 보기 | 브라우저 데모 질문 시연 | ✅ |

**순서 근거:**
- RAG-1(조회)을 검색보다 먼저: 쉬운 워밍업 + `/citations` 는 이후 전 단계의 디버깅 도구
- RAG-5(eval)를 답변 생성보다 먼저: 검색 품질을 숫자로 고정한 뒤 진행 — 변경 시 퇴행 감지 (마스터 5-5 "한 번에 하나씩 수정, 수정 후 eval")
- 결정 지점 2곳: RAG-2 한국어 검색 방식, RAG-4 융합 방식 — 실데이터 검증으로 그때 결정

### RAG-0 구현 기록 (2026-07-02)

- **`backend/db.py`**: connection pool 모듈 — `.env` 의 DATABASE_URL 로드,
  ThreadedConnectionPool min 1 / max 5 (마스터 6-4 "무료 DB 연결 수 제한 — pool 작게"),
  `get_conn()` 컨텍스트 매니저로 대여→반납 보장, 예외 시 자동 rollback
- **`/health`**: 실제 DB 쿼리로 chunks·embeddings 행수 반환, DB 장애 시 `degraded` 상태 응답
- **검증**: `{"status":"ok","db":"ok","chunks":419882,"embeddings":419882}`, 12회 연속 호출로 풀 반납 확인

### RAG-1 구현 기록 (2026-07-02)

- **4개 엔드포인트** (`backend/main.py`):
  - `/committees` — 9개 위원회 + 정식명칭 + 정책분야 + 회의 수
  - `/meetings?committee=&date_from=&date_to=` — 위원회·기간 필터, 최신순
  - `/speakers?committee=&q=` — 발언 수 순, 이름 부분 검색
  - `/citations/{chunk_id}` — 원문 발언 전문 + 앞뒤 맥락 + PDF 파일명·페이지 (신뢰 설계의 핵심), 404 처리
- **검증**: `/speakers?q=이진숙` → 후보자(2,320)·위원장(1,079)·증인(317) 경력 변화 확인,
  `/citations` → 이진숙 인사청문회 모두발언 원문 + p.3 + 직전 맥락(최민희 위원장) 반환
- **API 문서**: http://127.0.0.1:8000/docs (FastAPI 자동 생성)

### RAG-2 구현 기록 (2026-07-02)

- **결정: pg_trgm (부분 문자열) 채택, FTS 기각** — 실측: FTS(simple)는 조사 붙은 형태를
  놓침 ("티메프" 99건 중 78건만, 21% 손실). 부분 문자열은 조사 무관
- **인덱스**: `idx_chunks_text_trgm`, `idx_chunks_speaker_trgm`, `idx_chunks_role_trgm` (GIN, gin_trgm_ops)
  — 인덱스 전 374ms → 후 DB 4.6ms
- **모듈**: `backend/search_keyword.py` (OR 토큰 + 점수: 발언자+3 / 역할+2 / 구문+2 / 토큰+1),
  `backend/aliases.py` (기관 20여 그룹 + 인물 한자 2건 + 사건 통칭), `/search/keyword` 엔드포인트
- **검증**: "티메프 피해자 구제"→윤한홍 구제 발언·류광진 티몬 대표 / "AI 기본법"→유상임 장관 제정안 /
  "유영하"→柳榮夏 위원 본인 발언 1~3위 / "경찰청장 직무대행"→유재성 직무대행. 응답 72ms
- **과정에서 잡은 함정 3개**:
  1. 별칭 사전의 한자를 표준 코드로 적어 DB의 호환용 한자(U+F9C9)와 불일치 → 이스케이프 표기로 양쪽 등록
  2. `NULL ILIKE` → 점수 합계가 NULL → `ORDER BY DESC` 에서 NULL 이 1위로 → `COALESCE(…, 0)` 필수
  3. Windows `localhost` 가 IPv6 우선 시도로 +2초 지연 → `127.0.0.1` 사용 (프론트 연결 시 주의)
- **v1.3 추가 발견**: `관련의안`·`표시는` 등 의안 목록 잡음 발언자 잔존 (점수 수정 후 하위로 밀려 실해 없음,
  v1.3 재처리 때 비발언 차단 목록에 추가)

### RAG-3 구현 기록 (2026-07-02)

- **모듈**: `backend/search_vector.py` — 질문을 text-embedding-3-small 로 임베딩(ETL-8 과 동일 모델·의미 공간)
  → `embeddings_openai` HNSW 코사인 검색 → 필터(위원회/기간/발언자)
- **필터+HNSW 주의**: 필터 병용 시 후보 부족 방지를 위해 `SET LOCAL hnsw.ef_search = 100` (기본 40)
- **엔드포인트**: `/search/vector` (q, committee, date_from/to, speaker, limit)
- **검증 5/5**: "공영방송 지배구조 개선"→김태규 부위원장 발언 1위 / "전세사기 피해자 지원 대책"→국토위 청문회 채택 안건 /
  "병사 월급 인상과 국방 예산"→국방위 예산 질의 / committee=과방위 필터 / speaker=김태규 필터 모두 정확
- **응답 속도**: 첫 호출 3.5초(OpenAI 클라이언트 웜업), 이후 0.5~1.1초 (대부분 임베딩 API 시간, DB 검색은 수 ms)

### RAG-4 구현 기록 (2026-07-02)

- **결정: RRF (Reciprocal Rank Fusion, k=60) 채택** — 키워드 점수(정수)와 코사인 유사도(0~1)는
  눈금이 달라 직접 합산 불가 → 순위만 사용해 `Σ 1/(60+rank)` 합산. 양쪽 상위 공통 문서가 자연히 1위
- **모듈**: `backend/search_hybrid.py` — 각 축 상위 30개 융합, is_short 페널티 ×0.8,
  동일 turn 조각 중복 제거(최고 순위만), `/search/hybrid` 엔드포인트
- **검증 — ETL-8 실패 질문의 회복이 핵심**:
  - "AI 기본법 논의의 핵심 쟁점": 벡터 단독(ETL-8)에선 의사진행 잡음만 → 하이브리드에선
    이정헌 위원 "AI 기본법의 핵심 내용 가운데 AI 투명성 확보"(kw#4+vec#12 → RRF 1위) ✓
  - "티메프 사태 피해자 구제" → 민병덕·천준호 티몬·위메프 정산 사태 발언 ✓
  - "공영방송 지배구조 개선" → 김현 위원(kw#6+vec#2 양축 발견) 1위 — 주제형 강점 유지 ✓
  - "유영하 위원의 질의" → 전부 柳榮夏 실제 질의 (별칭 사전 통과) ✓
- **응답에 융합 근거 포함**: `found_in`(keyword/vector/양쪽), `kw_rank`/`vec_rank`, `rrf_before_penalty` — 디버그·신뢰 표시용
- **속도**: 2.3~3.8초 (대부분 질문 임베딩 API + 두 축 순차 호출 — 병렬화는 추후 최적화 여지)

#### Weighted RRF 개선 (2026-07-02, 같은 날 2차)

- **변경**: 균등 가중 → `KEYWORD_WEIGHT=1.2 / VECTOR_WEIGHT=1.0`, `SHORT_PENALTY 0.8→0.9`
  - 근거: 국회 회의록 질문은 인물명·기관명·법안명·사건명 등 고유명사 비중이 높아 키워드 축을 소폭 신뢰
  - 식: `score = Σ weight × 1/(60+rank)`, is_short 이면 최종 ×0.9
  - 질문 유형별 자동 가중치는 보류 — 고정 가중치 먼저, eval(RAG-5) 측정 후 판단
- **검증** (수정 전 baseline 저장 → 7개 질문 상위 10 비교):
  - 7/7 질문 top1 유지 (퇴행 없음), 스펙 예상 계산과 실제 값 일치 (before=0.03264, final=0.02937)
  - 좋아진 점: 키워드 확실 매치가 상위로 — 티메프→박형수 직접 발언·류광진 티몬 대표 답변 진입,
    이진숙→본인 발언 3건 추가 진입, 제2차관→차관 본인 발언 진입
  - 애매한 점: 신규 진입 일부는 초단문("수정의견에 동의합니다")이나 경계성 매치 —
    **정확한 우열 판정은 RAG-5 eval(Recall@10·MRR)로 정량 비교 필요**

### RAG-5 구현 기록 (2026-07-02)

- **평가셋 v1 (18문항)** → hybrid 100% 포화(천장) → **v2 (63문항)로 확장**
  - prototype(National_Assembly_2)의 75문항 세트에서 45문항 이식 — 12+ 유형 체계 계승
    (proper_noun/person/topic/mixed/comparison/date_based/multi_chunk/numerical_fact/
    cause_effect/quote_exact/aggregation/cross_committee/unanswerable)
  - 이식 시 우리 코퍼스 기준 재검증: prototype 에서 unanswerable 였던 일부(국민연금×외통위 등)는
    우리 데이터엔 존재 → 답변 평가용으로 보류. 원본 75문항은 `data/eval/prototype_75q_full.json`
    보존 (RAG-6/7 답변·Grounding 평가용 — answer/grounding_level/manual_grades 포함)
  - **unanswerable 4문항은 반전 채점**: 상위 10에 기준 일치 0건이면 통과 (REFUSED 판정의 기초)
- **정답 판정은 기준(criteria) 방식**: text_any/text_all/speaker_any/committee_any/date_any/mode —
  chunk_id 재배열에도 유효. 판정은 전문(full text) 기준. 평가셋: `data/eval/retrieval_eval_set.json`
- **v2 첫 측정 (개선 전)**: hybrid R@5=0.746, MRR=0.671 — **date_based 0.00**, comparison 0.33 발견

#### eval 주도 개선 사이클 1 (2026-07-02)

- **진단**: ①질문 속 날짜는 메타데이터라 본문 검색으로 불가능(date_based 0.00),
  ②조사 붙은 일반어 토큰("정부의","반응은")이 키워드 점수 오염(긴 자연어 질문 열화)
- **수정**: `backend/query_parser.py` 신설 — extract_filters(날짜·위원회→검색 필터 자동 변환,
  "2025년 7월 14일"→exact, "2024년 6월"→월 범위, 정식명→약칭 매핑) +
  content_tokens(조사 제거 + 불용어 필터). hybrid 가 자동 적용, keyword 토크나이저 교체
- **결과 (v2 63문항, k=10)**:

| 모드 | Recall@5 | Recall@10 | MRR@10 | unanswerable |
|------|----------|-----------|--------|--------------|
| keyword | 0.695 → **0.831** | 0.729 → 0.898 | 0.602 → 0.774 | 4/4 |
| vector | 0.644 | 0.746 | 0.530 | 4/4 |
| **hybrid** | **0.746 → 0.949** | 0.780 → **0.966** | 0.671 → **0.885** | 4/4 |

  - date_based **0.00 → 1.00 (MRR 1.00)**, comparison 0.33→1.00, topic 0.70→1.00, person 0.84→0.95
  - 실패 13건 → 2건 (윤후덕+재외국민 표현 불일치, 대일외교 시기별 — 고난도 잔여)
- **리포트**: `data/v1/reports/retrieval_eval_*.json` — 이후 검색·가중치 변경 시마다 재실행해 비교 (마스터 5-5)

### RAG-6 구현 기록 (2026-07-03)

- **`backend/answer.py`** — 단일 모듈, qa/report 는 MODE_CONFIG 설정만 분기
  (설계 결정 2026-07-03: 간단 질의 vs 정책 브리핑 **모드 차등** — 파이프라인 공유,
  검색 개수·맥락 조립·프롬프트·max_tokens 만 다름)
  - **qa**: 상위 5 전문(R@5=0.949 근거), 3~6문장, max_tokens 700
  - **report**: 상위 10 전문(R@10=0.966) + **인접 턴 보조 맥락**(같은 회의 순번 ±1,
    조각 chunk_index 순 복원 후 500자 절단, 근거 턴과 중복 제거),
    개요/쟁점별 정리/주요 발언 근거/논의의 한계(+조건부 정책적 시사점) 구조, max_tokens 2000
  - 검색 응답의 snippet(200자) 은 쓰지 않고 chunk 전문 일괄 재조회. chunk_id 는 LLM 에
    미노출 — 번호↔chunk_id 매핑은 코드가 보관해 citations 에서 복원
  - **근거 부족 3단계** (RAG-7 Grounding 의 기초): 충분→정상 / 일부→확인분만 답하고 나머지 명시 /
    전무→고정 문구 "제공된 회의록에서 확인할 수 없습니다". 검색 0건이면 LLM 미호출 (비용 0)
  - 한자 이름 `柳榮夏(유영하)` 병기 — LLM 번역이 아니라 aliases.py 사전으로 코드가 처리
  - **인용 후검증**: 답변 속 `[n]` 파싱 → cited_numbers / invalid_citations(범위 밖 = 프롬프트 위반 신호),
    citations 엔 실제 인용된 근거만 (chunk_id 포함 → 프론트 `/citations/{chunk_id}` 연결)
- **`POST /answer`** (main.py): mode 는 Pydantic Literal 검증(422), OpenAIError → 502
- **테스트**: `tests/test_answer.py` 34케이스 ALL PASS — LLM·DB 없는 순수 로직
  (인용 파서·근거 조립·한자 병기·인접 턴 복원·0건 고정 문구·모드 차등)
- **스모크** (eval 셋 유형별 8문항 qa + 2문항 report, LLM 10회):
  - invalid citation **0건**, 모든 사실 주장에 `[n]` 부착, unanswerable **2/2 거절**, 한자 병기 정상
  - 비용 실측: qa ~$0.0005/질문, report ~$0.0017/질문. 응답 시간: qa 2.5~13초, report 10~18초
  - 관찰 ①: 거절 문구가 어순 변형될 수 있음("제공된 회의록에서 X는 확인할 수 없습니다")
    → **RAG-7 REFUSED 판정은 exact match 대신 부분 문자열 매칭으로**
  - 관찰 ②: 간혹 답변 끝에 대상 없는 "이 부분은 … 확인할 수 없습니다" 꼬리 문장 — 프롬프트 미세조정 여지

#### 프롬프트 보강 + 상투구 후처리 (2026-07-03, 같은 날 2차 — RAG-7 전제 작업 선행)

- **계기**: "북한 오물풍선 쟁점을 여야별로 정리" 실사용 시험에서 **LLM이 근거에 없는
  정당 소속을 추측 생성** (엄태영 위원이 여당·야당 양쪽에 등장하는 자기모순까지).
  원인: speakers 테이블에 정당 정보가 없고 회의록 원문에도 거의 안 나옴 —
  "근거 없는 내용 금지"가 소속 분류라는 우회로로 새는 케이스
- **프롬프트 2줄 보강** (_COMMON_RULES): ① 정당·진영은 근거에 명시된 경우에만,
  없으면 발언자별 정리 + 확인 불가 1회 명시, 정당 무관 질문엔 언급 자체 금지
  ② 확인 불가 문구는 구체적 대상이 있을 때만 — 대상 없는 꼬리 문장 금지
- **후처리 `strip_boilerplate()` 추가**: 2회 프롬프트 반복에도 gpt-4o-mini 가
  금지 상투구를 간헐 출력 (실측) → 순응에 의존하지 않는 결정론적 제거.
  답변 **끝** 문장만 검사: 대상 없는 꼬리("이 외의/이 부분은 … 확인할 수 없습니다") +
  정당 무관 질문의 자진 정당 문구. 구체적 대상 거절·전체 거절(REFUSED 신호)은 보존.
  테스트 8케이스 추가 (총 42 ALL PASS)
- **검증 (4문항 재실행)**: 티메프·계엄·유영하 → 거절 문구 없는 깨끗한 답변 (FULL 판정 가능),
  오물풍선 → "소속 정당 확인 불가" 명시 (PARTIAL 판정 — 의도된 동작)
- **잔여 한계 → 3차 방어로 해결 (같은 날)**: 시스템 프롬프트 보강 후에도 질문의 "여야별"
  요구를 모델이 우선해 여당/야당 섹션을 유지 (재실행에서 같은 위원 양 진영 등장 재발 —
  비결정적). **원인 분석**: ①정당 정보가 데이터에 없어 모델이 발언 논조로 진영을 추측,
  ②국회 화법 특성상 상대 논리를 인용·요약하는 발언이 반대 진영으로 오분류,
  ③코퍼스 기간(2024-05~2026-06) 중 정권교체로 여야 자체가 시점 의존적.
  **처치**: `build_user_message()` — 질문에 `여야|정당|진영|소속` 감지 시 user 메시지의
  질문 바로 뒤에 안내문 자동 첨부 (시스템 프롬프트보다 질문 인접 위치가 준수율 높음, 실측).
  검증: 오물풍선 질문 2회 연속 여야 섹션 0건, 쟁점별+발언자별 정리로 전환, 정당 확인 불가
  명시 유지. 테스트 4케이스 추가 (총 46 ALL PASS)
- **근본 해결은 3단계**: 의원-정당 매핑 테이블 (국회 공공데이터) + **시점별 여야 판정**
  (정당 × 발언 날짜 × 당시 집권당 — 정권교체 때문에 정당만으론 여야 확정 불가)
- **열린국회정보 API 사전 검증 완료 (2026-07-03)** — 키 발급·`.env`의 `OPEN_ASSEMBLY` 등록,
  호출 성공. 정당 모듈 구현 시 알아야 할 실측 사항:
  - 서비스: `ALLNAMEMBER` (역대 의원 3,295명) — `https://open.assembly.go.kr/portal/openapi/ALLNAMEMBER?KEY=…&Type=json&pIndex=1&pSize=1000`
  - **브라우저 User-Agent 헤더 필수** (파이썬 기본 UA는 HTTP 400 — 게이트웨이 차단)
  - 유용 필드: `NAAS_NM`(한글명), **`NAAS_CH_NM`(한자명 — 유영하 별칭 사전 확장에 활용 가능)**,
    `PLPT_NM`(정당 이력, `/` 구분 — 당적 변경 추적 가능), `GTELT_ERACO`(당선 대수, 쉼표 목록)
  - 서버측 대수 필터는 미적용됨 → 전체 4페이지 수집 후 `GTELT_ERACO`에 "제22대" 포함 여부로
    클라이언트 필터링

### RAG-7 구현 기록 (2026-07-03)

> 스펙: `docs/rag7_query_spec.md` (설계 확정 후 구현 — threshold 설정값화는 사용자 결정)

- **`backend/grounding.py`** — 규칙 기반 + 유사도 사전차단 (순수 함수, 비용 0, 결정론적)
  - 사전차단: 검색 0건→NONE / 벡터 최고 유사도<threshold AND 키워드 0건→REFUSED (LLM 미호출)
  - threshold 는 `.env` `GROUNDING_SIM_THRESHOLD=0.4` (하드코딩 금지 — 무작위 기준선 0.386 실측
    기반 경험값이라 조정 가능해야 함), 호출 시점에 읽어 테스트에서 env 변경 검증
  - 사후 판정: 인용×거절문구 → FULL/PARTIAL/REFUSED, 무인용 주장→PARTIAL+ungrounded,
    invalid_citations→FULL 강등
  - **거절 감지 2단 패턴 (스모크 실측으로 진화)**: LLM 이 고정 문구를 변형함 —
    "확인되지 않습니다"·"포함되어 있지 않습니다"·"언급이 없습니다" 3종 실측.
    인용 있는 답변엔 엄격 패턴만 (발언 인용 "근거가 없다고 지적[1]" 오탐 방지),
    인용 없는 답변엔 넓은 패턴 (거절 아니면 환각이라 넓게 잡는 게 안전).
    새 변형 발견 시 패턴+회귀테스트 추가 — **query_logs 의 PARTIAL+ungrounded 행이 후보 큐**
- **`POST /query`** (스텁 교체) — 하이브리드 검색 1회 → 사전차단 → generate_answer(**hits 주입**,
  이중 검색 방지) → 사후 판정 → query_logs 저장 → `{query_id, grounding, latency_ms, answer, citations, …}`
  - `search_hybrid.py` 에 `vec_score`(벡터 원점수) 보존 필드 추가 (기존 응답 호환)
  - speaker 필드 제거 (하이브리드 미지원), mode Literal 검증
- **query_logs** (`db/schema.sql` + DB 생성) — 답변·등급·비용·지연 기록, 로그 실패해도 답변 반환
  (try/except 격리). **`/feedback`** 구현: query_id 로 rating UPDATE, 없으면 404, UUID 형식 422
- **테스트**: `tests/test_grounding.py` 19케이스 ALL PASS (사전차단·threshold env·판정표·변형 문구·오탐 방지)
- **스모크 (10문항 + feedback)**: **unanswerable 4/4** (REFUSED 3 + 사전차단 NONE 1 — LLM 비용 0),
  **정상 6/6 FULL**, /feedback 반영·404 확인. query_logs 30행 적재 (FULL 17/REFUSED 7/PARTIAL 3/NONE 3)
- 사전차단 NONE 응답 0.4~0.9초 (LLM 미호출), qa 정상 4~8초

### RAG-8 구현 기록 (2026-07-03)

- **컴포넌트 분리** (App.jsx 는 상태 조립만): `api.js`(fetch 래퍼, 502→친화 메시지),
  `QueryForm`(Enter 제출, qa/report 모드 토글), `AnswerPanel`(grounding 배지 4색,
  react-markdown 렌더, **본문 [n] 클릭→출처 카드 스크롤+하이라이트**, ungrounded 경고 배너,
  피드백 👍👎, latency·비용 메타), `SourcePanel`(sources 전체 + "인용됨" 표시 —
  인용 안 된 근거도 투명 노출), `SourceModal`(전문+앞뒤 맥락+PDF 위치, ESC/바깥 클릭 닫기)
- **신규 의존성**: react-markdown 하나만
- **함정 수정 2건**: ① API_BASE `localhost`→`127.0.0.1` (Windows IPv6 +2초),
  ② CORS 에 `http://127.0.0.1:5173` 추가 (페이지를 어느 주소로 열어도 동작)
- **검증**: oxlint 0건, vite build 통과, 브라우저 origin 헤더로 /query·/citations CORS 포함
  end-to-end 확인 (FULL+출처 5건, 모달용 전문·맥락·PDF 위치 응답 정상). 브라우저 클릭 시연은
  사용자 확인으로 마감
- 실행: 백엔드 8000 + `cd frontend && npm run dev` (localhost:5173)

**2단계 완료 기준** (마스터 문서 4-12 대응) — **4/4 달성, 2단계 완료 (2026-07-03)**:
- [x] 질문하면 출처 달린 답변이 나온다 — `POST /query` (RAG-7)
- [x] 원문 발언을 클릭해 확인할 수 있다 — 출처 카드 → 원문 모달 (RAG-8)
- [x] 근거 부족 질문에 확인 불가(REFUSED)로 답한다 — Grounding 판정 (RAG-7)
- [x] eval 기준선이 있고 변경 후 점수 비교가 가능하다 — RAG-5 (hybrid R@5=0.949)

## 3단계 1차 — 정당 모듈 구현 기록 (2026-07-03)

> 스펙: `docs/party_module_spec.md` (API 실측 탐사 → 설계 → 구현)

- **`scripts/build_members.py`**: 열린국회정보 ALLNAMEMBER 수집(UA 필수) → 22대 320명 필터
  → `data/members/*.json` 보존 + DB `members` 적재 (DELETE+재삽입, 행수검증).
  **위성정당 34명(국민의미래 18·더불어민주연합 16)은 표기 그대로 유지** (2026-07-03 사용자
  결정 — 모정당 치환 기각). 여야 판정만 party.py `SATELLITE_PARENT`로 모정당 기준
  (예: `국민의미래(당시 여당)` — 국힘 정권에서 야당으로 오계산되는 것 방지)
- **`backend/party.py`**: `party_label(발언자, 회의날짜)` → "국민의힘(당시 야당)" —
  **시점별 여야 판정** (정권교체 2025-06-04 경계, RULING_PERIODS 상수).
  NFKC 정규화로 호환용 한자 발언자(柳榮夏)도 매칭. 서로 다른 정당 동명이인·미매칭은
  None (틀린 라벨보다 무표기 — 신뢰 원칙). members 는 첫 사용 시 1회 로드 캐시
- **answer.py 주입**: 근거 블록 speaker 줄에 `[정당(당시 여야)]` 코드 표기 — LLM 정당 추측
  원천 차단. `_PARTY_GUARD` 는 "표기된 것만 사용" 안내로 교체. `_source_summary`에 party 필드
  → 프론트 출처 카드에도 표기 (SourcePanel)
- **aliases.py 자동 확장**: `hanja_aliases.json`(320쌍) 로드 — 한자 검색 별칭 수동 2명→전체 자동.
  역인덱스를 합집합 병합으로 바꿔 수동 그룹(호환용 한자)과 자동 그룹(표준 한자) 공존
- **grounding 판정 개선**: report 의 "## 논의의 한계" 섹션은 거절 스캔에서 제외 —
  프롬프트가 요구한 정직성 장치가 모든 브리핑을 PARTIAL 로 자기강등시키던 구조 문제 해결
- **테스트**: test_party.py 17케이스 (여야 경계일·NFKC·동명이인·위성정당·별칭 병합) +
  test_grounding 2케이스 추가 — 전체 회귀 ALL PASS
- **검증**: "오물풍선 쟁점 여야별 정리" → **grounding FULL**, 여야 섹션이 정확한 라벨로 부활
  (김병주 [더불어민주당(당시 여당), 2025-11 발언] / 엄태영 [국민의힘(당시 야당), 2025-09] /
  이만희 [국민의힘(**당시 여당**), 2025-04] — 시점별 판정 실증). 정당 무관 질문(티메프) FULL 유지
- **발언 자격(role) 게이트 (같은 날 2차 — 사용자 규칙)**: 정동영(의원 겸 통일부장관)이
  장관 발언에서 "여당"으로 분류되는 버그 발견 → **이름이 아니라 자격으로 판정**.
  국회의원 자격만 정당·여야 라벨 / 행정부 자격(장관·청장·행정기관 위원장 등)은 **"정부측"** /
  **후보자(…후보자)는 무표기·직함 그대로** (아직 행정부 아님 — 사용자 규칙 2차) /
  증인·참고인·진술인은 출석 지위 그대로(프롬프트 분류 규칙) / 국회 스태프·미상은 무표기.
  실측: 정동영 통일부장관후보자→무표기, 김석기 위원장→정당 유지(오폭 없음), 티몬 대표→무표기.
  test_party.py 13케이스 추가 (총 33) — 상세는 party_module_spec.md "발언 자격 게이트"
- **후처리 미세수정**: strip_boilerplate 가 report 의 "## 논의의 한계" 섹션 끝 문장을
  지워 빈 제목만 남기던 부작용 → 한계 섹션이 있는 답변은 건너뛰도록 수정
  (grounding 판정이 이미 그 섹션을 제외하므로 지울 이유 없음)
- **알려진 한계**: 최종 당적 스냅샷 (임기 중 탈당 미추적 — 향후 이력 API 보완 검토),
  LLM 의 섹션 구성 방식은 실행마다 다를 수 있음 — 여야 섹션 제목이 시점 혼합을 단순화하거나
  한계 섹션 성실도가 실행마다 다름 (정당 라벨 자체는 항상 코드가 보증)

### 검색 개선 — 복수 위원회 질문 대응 (2026-07-03)

- **발견 (실측)**: "외교위와 국방위에서 오물풍선을 어떻게 다뤘나" →
  ① "외교위"가 미등록 표기라 인식 실패 ② 등록된 "국방위"만 필터로 잡혀 외통위 근거가
  검색에서 원천 배제 → **데이터에 있는 걸 "확인할 수 없다"고 답하는 거짓 부정**
  (ETL-8 "있는데 못 찾음" 문제의 필터 버전). 3개 위원회 질문도 첫 번째만 잡힘
- **수정 3종**:
  ① `COMMITTEE_MAP` 통용 별칭 확장 (외교위·외교위원회→외통위, 산자위, 국토교통위 등)
  ② `extract_filters` 가 위원회를 **전부 감지** (search→findall, 순서 유지·중복 제거) —
    반환이 committees 리스트로 변경, keyword/vector 검색은 `co.name = ANY(%s)` 필터
  ③ **위원회별 근거 균형 배분** (`_balance_by_committee`): 복수 위원회 질문이면
    limit÷n 씩 quota 우선 확보, 부족분은 전체 순위로 채움 — 발언량 많은 위원회의
    상위 독식 방지 (없는 근거를 만들지는 않음 — 기회 보장일 뿐)
- **검증**: 실측 사례 재실행 → 출처 국방위 2+외통위 3, 양쪽 모두 답변, **FULL**.
  3개 위원회 질문 → 국토위4+정무위1 (외통위는 부동산 논의가 실제로 없음 → PARTIAL 정직 표시).
  tests/test_multi_committee.py 15케이스 + 전체 회귀 ALL PASS
- **eval 재실행**: hybrid R@5=0.949 / R@10=0.966 동일, **MRR 0.885→0.894 소폭 개선** — 퇴행 없음
- **후속 (같은 날): 위원회 오배치 수정** — 답변이 국방위 발언(성일종)을 외통위 문단에 배치하는
  조직 오류 실측 (인용은 유효해서 FULL 인데 내용 배치가 틀림 — 판정 사각지대).
  ① **근거 블록을 위원회별 섹션으로 그룹핑** (`build_source_block(group_by_committee=)` —
  복수 위원회 질문일 때만, 번호 유지. 정당 라벨과 같은 원리: 모델이 추론할 필요를 구조로 제거)
  ② 프롬프트 규칙 "근거를 다른 위원회의 논의로 옮겨 서술하지 않는다".
  검증: 같은 질문 2회 오배치 0건 (문단 위원회↔인용 위원회 대조 휴리스틱), 단일 주제 퇴행 없음.
  잔여 과제(POL-7 기록): 발언자-인용 정밀도 및 오배치 자동 검출은 답변 평가셋에서 체계화

## 3단계 세부 로드맵 (POL-0 ~ POL-9)

> 마스터 문서 3단계("정치학 도메인 MVP — 쟁점/시계열/행위자 분석")를 ETL·RAG 방식으로
> 세분화 (2026-07-03). 재료: ETL-5 enrichment 필드(미검증), members 테이블(정당 모듈),
> query_logs, eval 프레임워크.

| # | 이름 | 내용 | 완료 기준 | 상태 |
|---|------|------|----------|------|
| POL-0 | 행위자 기초 (정당 모듈) | 22대 의원-정당 매핑 + 시점별 여야 판정 + 발언 자격 게이트 + 근거 블록 주입 | "여야별" 질문 grounding FULL | ✅ |
| POL-1 | enrichment 실태 조사 | ETL-5 필드(policy_domain·bill_refs·utterance_type·stance_signals·mentions) **커버리지·품질 리포트** — 만든 뒤 한 번도 분석에 안 쓴 데이터의 실태부터 (게이트의 게이트 교훈) | 필드별 커버리지·정확도 스팟체크 리포트, 사용 가능/불가 판정 | ✅ |
| POL-2 | 행위자 프로필 API | `/actors/{name}`: 발언 통계(위원회·기간·정책 도메인 분포), 정당·여야 이력, 대표 발언 — 기존 chunks 집계라 데이터 준비 완료 | 특정 의원 curl 한 번에 프로필 JSON | ✅ |
| POL-3 | 쟁점 사전 구축 | 이슈 정의 방식 **결정 지점**: LLM 클러스터링 vs 수동 시드 사전(하이브리드 검색으로 청크 확장). 22대 주요 이슈 20~30개 + 이슈↔청크 매핑 | 이슈 목록 + 매핑, 무작위 스팟체크 통과 | ⬜ |
| POL-4 | 쟁점 타임라인 | 이슈별 월별 발언량·참여 위원회·주요 회의 시계열 API | `/issues/{id}/timeline` 정합성 스팟체크 (원본 대조) | ⬜ |
| POL-5 | 입장(stance) 분석 | 이슈×행위자 입장(찬성/반대/우려/중립) — stance_signals 규칙 vs LLM 판정 **결정 지점**. 모든 입장에 근거 발언 인용 필수 (신뢰 원칙) | 이슈 1개의 행위자별 입장+근거 매트릭스 | ⬜ |
| POL-6 | 여야 대립 구도 | 이슈별 여야(발언 시점 기준) 입장 분포 — POL-0 정당 모듈 × POL-5 입장 결합 | "이슈 X의 여야 입장 차이" 질문에 근거 있는 구도 응답 | ⬜ |
| POL-7 | 분석 eval | 입장 판정 수동 라벨 세트(30~50건) + 타임라인 정합성 검사 — 이후 변경마다 재실행 (마스터 5-5) | 기준선 점수 기록 | ⬜ |
| POL-8 | 분석 통합·report 확장 | 분석 API 정리 + report 모드 브리핑에 타임라인·행위자·구도 데이터 주입 | 브리핑에 시계열·행위자 근거 포함 | ⬜ |
| POL-9 | 프론트 분석 뷰 | 이슈 대시보드(타임라인 차트·입장 매트릭스), 의원 프로필 화면 | 브라우저 데모 시연 | ⬜ |

### POL-1 구현 기록 — enrichment 실태 조사 (2026-07-03)

- **방법**: `scripts/enrichment_audit.py` — SQL 분포·커버리지 통계 + 필드별 무작위 샘플
  (정확도 25건 + 누락 참고 10건, seed 고정 재현 가능) → Claude 원문 대조 판독.
  리포트: `data/v1/reports/enrichment_audit_20260703_*.json`

**필드별 판정 (3단계 분석 재료 사용 가능 여부):**

| 필드 | 실측 | 판정 |
|---|---|---|
| `policy_domain` | 위원회-도메인 조합 9 = 위원회 수 9 (**완전 1:1**) | ❌ **무가치** — committee 컬럼과 정보량 동일. 분석에선 committee 사용 |
| `stance_signals` | **neutral 97.9%** (positive 0.6%, negative 1.4%). 스팟체크 명백 오류 6/25 | ❌ **사용 불가** — 원인: 공백분리 토큰 exact match 가 한국어 활용형을 못 잡음 ("심각한"·"잘못된"·"우려도" 미매치 실측) |
| `bill_refs` | 커버리지 2.2%. 최빈값 1·2위가 "관한 특별법"·"위한 특별법" (**정규식 조각**), 조문(제N조) 대량 혼입, "회부됨국제개발협력기본법" 공백소실 병합. 단순형("방송법") 미포착 | ❌ **사용 불가** — 정밀도·재현율 모두 낮음 |
| `utterance_type` | statement 65% / question 34% / motion 0.4%. 스팟체크 **25/25 정확** | 🔶 **조건부 사용 가능** — question/statement 이진은 신뢰. motion 은 과소검출("동의합니다" exact 만) — 쓰지 말 것 |
| `mentions` | 커버리지 8.6%, 스팟체크 오탐 0 (문자열 매치 특성상 정밀도 ~100%) | ✅ **조건부 사용 가능** — 한계: 별칭 미정규화(금융위≠금융위원회 이중 카운트), 목록 밖 기관 누락(헌재·선관위·감사원·국정원 등), 직책 포함 매치("법무부장관"→법무부) |

**후속 결정 (로드맵 반영):**
- **POL-5 결정 지점 해소**: stance_signals 불가 판정 → 입장 분석은 **LLM 판정 필수**
  (규칙 기반 선택지 기각 — 실측 근거 확보)
- **POL-3 제약**: 쟁점-청크 매핑에 bill_refs 를 쓰지 말 것 — 하이브리드 검색 기반으로 설계
- **POL-2 활용 가능 재료**: mentions(정규화 후) + utterance_type(question/statement) + members
- (선택) enricher v2 재설계는 v1.3 재처리 묶음에 포함 검토 — 활용형 매칭(stance),
  법명 사전 기반 추출(bill_refs). 단 POL-5 를 LLM 으로 가면 stance v2 는 불필요할 수 있음

### POL-2 구현 기록 — 행위자 프로필 API (2026-07-03)

- **`backend/actors.py` + `GET /actors/{name}`**: 발언 통계(turn 단위 집계 — 청크 분할
  중복 방지), 위원회·월별 분포(POL-4 시계열 준비물), **정권 구간별 여야 이력**
  (RULING_PERIODS × party_label 재사용), question/statement 비율(POL-1 검증 이진만),
  top 언급 기관(별칭 정규화 — `canonical_org`로 금융위/금융위원회 병합), 최근 발언 5건
  (is_short 제외, chunk_id → /citations 연결). 미등록 인물 party=null, 발언 없으면 404
- **party.py 에 `member_party()` 추가** (자격 게이트 없는 순수 조회 — 프로필용)
- **테스트**: test_actors.py 10케이스 (정규화·여야 이력) ALL PASS
- **스모크**: 김병주(민주, 국방위 1,655턴, 야당→여당 이력, question 56.7%, top 국방부) /
  유영하(**한자 별칭 매칭으로 2,219턴** — 한글 조회로 柳榮夏 발언 포착) /
  조태열(비의원 — party null) / 없는 이름 404

**순서 근거:**
- POL-1 을 모든 분석보다 먼저: ETL-5 enrichment 는 생성 후 검증 없이 쌓여 있음 —
  분석의 재료가 되는지 실태 조사부터 (chunks_quality_gate "게이트의 게이트" 교훈 재적용)
- 행위자(POL-2)를 쟁점(POL-3)보다 먼저: 데이터가 이미 준비됨 (members + chunks 집계) —
  워밍업이자 이후 입장 분석의 축
- 쟁점 사전(POL-3)이 시계열·입장·구도의 전제 — 여기가 3단계 최대 결정 지점
- 정식 eval 은 POL-7 이지만, 각 단계 완료 기준에 스팟체크를 내장 (eval 은 초반부터 — 마스터 4-11)
- 결정 지점 2곳: POL-3 이슈 정의 방식, POL-5 입장 판정 방식 — 실데이터 검증으로 그때 결정

## 코드 전수 검토 + 1차 수정 (2026-07-06)

> 전체 코드베이스 검토(병렬 리뷰 3축: 백엔드/ETL/프론트+테스트) + 외부 리뷰(친구) 지적을
> 통합해 수정 항목 도출 — 전체 목록·진행 상태는 **`docs/fix_checklist.md`** (12/34 완료,
> 34 = 검토 32건 + 평가 보고서 추가 2건).
> 총평: 알고리즘 코어(검색·grounding·정당 라벨)는 견고, 약점은 운영 경계면
> (재실행 안전성·에러 경로·배포 설정)에 집중.

### 1차 수정 완료 (체크리스트 12항목, 전부 검증 통과)

| 수정 | 파일 | 검증 |
|------|------|------|
| **재적재 시 임베딩 전량 유실 방지** — DELETE 의 ON DELETE CASCADE 가 embeddings_openai 까지 삭제 → 임시 테이블 백업 후 embed_text md5 동일분만 복원, 요약에 보존/유실 표시 | `scripts/jsonl_to_postgres.py` | 4,092임베딩 회의 재적재 → 전량 보존, 전체 419,882 불변 |
| 한글 IME 조합 중 Enter 조기 제출 — `isComposing` 가드 | `frontend/.../QueryForm.jsx` | lint + build 통과 |
| **검색 적중 발언의 turn 전문 복원** — hybrid 가 같은 turn 조각 중 1개만 남겨 긴 발언 맥락이 잘림 → `_fetch_texts` 가 turn 복원 (상한 4,000자, 초과 시 적중 조각 중심 창 + 경계 조각 부분 포함 `…` 표기) | `backend/answer.py` | 9조각(2만자) 발언 2,437→4,000자 복원, 단위테스트 6건 |
| **LLM 근거 블록 로그** — query_logs.source_block 컬럼 (+ALTER 마이그레이션). 이상 답변 사후 재현·답변 품질 평가셋(POL-7)의 재료. API 응답엔 미노출 | `backend/main.py` `answer.py` `db/schema.sql` | E2E: 실질의 1건 10,591자 저장 확인 |
| top_mentions 청크 단위 중복 카운트 — (org, turn_id) DISTINCT 쌍 집합 집계 (별칭 병합 이중 카운트도 방지) | `backend/actors.py` | 실 DB 프로필 조회 정상 |
| 입력 검증 — 날짜 `datetime.date` 타입(불량 날짜 422), rating 1~5(👍=5/👎=1), question 2~1,000자, comment ≤2,000자 | `backend/main.py` | 서버 기동 후 422 확인 |
| 임베딩 OpenAI 장애 시 500→502 — `/query`·`/search/vector`·`/search/hybrid` 의 임베딩 호출을 OpenAIError 처리로 | `backend/main.py` | — |
| **pytest 무조건 통과 함정 제거** — check() 가 print 만 하고 assert 없음 + `test_*` 명명으로 pytest 가 수집해 전부 초록 → assert 화, parser/quality_gates 도 test 함수화, 데이터 없으면 skip | `tests/` 7개 | pytest 33건 실수집·통과 + 직접 실행 병행 |
| stdout 재래핑 import 부작용 — pytest 캡처 충돌 원인 → 전부 `if __name__ == "__main__":` 가드 | `tests/` 7개 + `scripts/` 16개 | 직접 실행 출력 동일 |
| (검토 중 발견) test_actors↔test_party 가 `party._party_map` 전역 공유로 pytest 일괄 실행 시 상호 오염 → 각 테스트가 자기 맵 주입 | `tests/test_party.py` | assert 도입 직후 3건 실패로 표면화 → 수정 |
| (검토 중 발견) test_party 한자 상수가 리터럴 — 에디터 유니코드 정규화 시 NFKC 테스트 무력화 → 이스케이프 표기 (파일 자체 관례 준수) | `tests/test_party.py` | U+F9C9/U+67F3 코드포인트 확인 |
| scripts/requirements.txt 에 pdfplumber·openai 누락 | `scripts/requirements.txt` | import 전수 대조 — 누락 0 |

### 2차 수정 (2026-07-07) — ETL 재실행 안전성 묶음 (8항목, 누적 20/34)

> 계기: `docs/Making_LLM.md` 기준으로 본 평가(`docs/llm_comparison_report.md`)에서
> 기준 10(파이프라인)이 B — "조용한 유실" 위험이 남은 유일한 시급 영역으로 판정.
> v1.3 재처리(해시 chunk_id) 전에 재실행 안전성을 확보.

| 수정 | 파일 | 검증 |
|------|------|------|
| **원자적 쓰기** — 최종 경로 직접 쓰기 → tmp + os.replace (중단 시 반쪽 파일이 "완료"로 고착되던 문제) | `scripts/stage_io.py` 신설 + 5개 스테이지 | 중단 시뮬레이션: 반쪽 파일·잔해 0, 기존 파일 무손상 |
| **실패 전파** — 소스별 실패가 exit 0 으로 삼켜짐 → failures/{stage}_failures.txt 기록 + exit 1 (run_pipeline 감지) | 5개 스테이지 main() | 실패 기록·스테일 삭제 검증 |
| **정정본 PDF 감지** — 추출 시 source.sha256 지문 기록, already_done 이 해시 비교. 기존 767개 백필 | `scripts/extractor_v1.py` | 정정본 시뮬레이션 → 재추출 대상 감지 |
| **PDF 다운로드 무결성 + 증분** — .part 임시 + %PDF 매직 확인, 기본 증분(--refresh 전체) | `scripts/crawl_pdfs.py` | 오프라인 5케이스 (에러페이지 차단 등) |
| **임베딩 재시도 분리** — base APIError → 일시 오류(RateLimit/Timeout/Connection/5xx)만 재시도 | `scripts/embeddings_v1.py` | 401/400 즉시 실패 분류 확인 |
| **○ 마커 파서-게이트 통일** — 767개 전수 조사: ○(U+25CB) 줄 시작 0회 → 게이트를 파서 기준 [◯◎] 로 | `turns_quality_gate.py` + `parser_v1.py` 주석 | 실데이터 게이트 PASS |
| 잘못된 날짜 질문 500 — "13월"·"2월 30일"·ISO 오타는 필터 미적용(일반 텍스트) | `backend/query_parser.py` | 회귀 테스트 5건 (총 34건 통과) |
| index.html lang="ko" + 탭 제목 "국회 회의록 RAG" | `frontend/index.html` | lint + build 통과 |

- **재실행 무해성 실검증**: 5개 스테이지를 기재위 22개 source 로 재실행 — 기존 산출물
  전부 올바르게 스킵(재처리 0건), exit 0. 기존 데이터 무접촉.

### 미수정 주요 항목 (fix_checklist.md 참조, 14건 잔여)

- **서버 안정성**: 풀 고갈 시 즉시 PoolError(동시 6+ 요청 500), 죽은 연결 반납
- **배포 준비**: rate limit·인증 없음(비용 공격 무방비 — 배포 착수 조건), CORS·API 주소
  하드코딩, 죽은 env 키 3개, **HNSW·trgm 인덱스 생성 SQL 이 저장소에 없음**,
  LIKE 와일드카드 미이스케이프, 프론트 타임아웃/취소 없음
- **품질**: 청킹 문장분할 한국어 보강 (재임베딩 유발 — v1.3 재처리 묶음에 포함 권장)
- **중장기**: 답변 품질 평가셋(source_block 로그 축적 후 — POL-7 연계), 날짜 범위 질문
  (첫 날짜 하루로 축소), role=NULL 정당 오라벨, 22대 하드코딩 4파일 중복,
  HTTP API 계층 테스트, 로그 실패 관측성

---

## v1.3 개선 예정 (청크 ID 무효화 변경 묶음 — 다음 재처리 때 한 번에)

> 아래 두 항목은 청크 ID를 바꾸므로 재임베딩을 유발한다. 반드시 묶어서, 해시 ID를 먼저 적용할 것.

1. **해시 기반 chunk_id** — 현재 순번 기반(turn_0001)이라 파서 수정 시 ID가 전부 밀려 전량 재임베딩 발생 (v1.2에서 $1.26 재지출). 내용 해시(source_id+page+speaker+text 앞부분) 기반으로 바꾸면 내용이 안 바뀐 청크의 임베딩 재사용 가능
2. **잔여 발언 복구** — parser_dropped_headers.txt 의 잔여 1,001건 중 실제 발언 ~100건 (회장/경비대장/교수/검사/~관 희귀 접미사)

---

## 보안 원칙

- API 키, DB 비밀번호, DATABASE_URL은 코드에 절대 포함하지 않는다
- `.env`는 GitHub에 올리지 않는다 (`.gitignore` 등록됨)
- `.env.example`만 커밋한다
