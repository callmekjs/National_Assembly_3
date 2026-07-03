# ETL 파이프라인 v1 — 설계 및 실행 가이드

## 전체 흐름

```
incoming_data/{위원회}/*.pdf (767개)
  ↓
manifest_builder.py       — PDF 목록 스캔 + SHA-256 해시 기록
  ↓
extractor_v1.py           — pdfplumber로 페이지별 텍스트 추출
  ↓
normalizer_v1.py          — 잡음 제거 + section_type 분류 + segments 생성
  ↓
parser_v1.py              — 발언자 마커(◯/◎) 기준 턴 구조화
  ↓
turns_quality_gate.py     — 파싱 품질 검사 (BLOCK/WARNING)
  ↓
policy_enricher_v1.py     — 정책 도메인 메타데이터 추가
  ↓
chunker_v1.py             — RAG 검색용 청크 생성
  ↓
chunks_quality_gate.py    — 청크 품질 검사
  ↓
jsonl_to_postgres.py      — PostgreSQL 적재 (✅ 구현 완료)
  ↓
embeddings_v1.py          — OpenAI 임베딩 → pgvector 저장 (✅ 구현 완료)
  ↓
retrieval_eval.py         — 검색 품질 평가 (OK 구현 완료)
  ↓
pipeline_report.py        — 전 단계 현황 집계
```

**보조 도구:**

```
run_pipeline.py           — 파일 파이프라인 순차 실행기 (게이트 실패 시 중단)
etl_audit.py              — 무작위 청크 ↔ 원본 추출 텍스트 대조 감사
tests/test_parser_speaker.py   — 파서 발언자 추출 단위 테스트 (실측 50케이스)
tests/test_quality_gates.py    — 게이트 자체 검증 ("게이트의 게이트", 4케이스)
```

---

## 데이터 레이어

| 레이어 | 경로 | 파일 | 설명 |
|--------|------|------|------|
| Raw | `data/v1/extract/` | `pages.jsonl` | PDF 원문에 가까운 상태 |
| Normalized | `data/v1/normalized/` | `normalized.jsonl` | 구조 정리 완료 |
| Parsed | `data/v1/parsed/` | `turns.jsonl` | 발언 단위 구조화 |
| Enriched | `data/v1/enriched/` | `enriched_turns.jsonl` | 정책 도메인 메타 추가 |
| Chunks | `data/v1/chunks/` | `chunks_v1.jsonl` | RAG 검색 최종 청크 |
| Reports | `data/v1/reports/` | `*.json` | 품질 리포트, 파이프라인 리포트 |

---

## 단계별 상세

### manifest_builder.py

PDF 원본 목록을 기록한다. 증분 처리의 기준점으로 사용한다.

출력: `data/v1/manifest.jsonl`

```json
{
  "source_id": "과방위_20240611_52074_52074",
  "committee": "과학기술정보방송통신위원회",
  "folder": "과방위",
  "file_name": "20240611_52074_52074.pdf",
  "file_hash": "sha256:...",
  "file_size": 123456,
  "date_hint": "20240611",
  "status": "pending"
}
```

---

### extractor_v1.py

pdfplumber로 PDF를 페이지별 plain text로 추출한다.

출력: `data/v1/extract/{source_id}/pages.jsonl`

```json
{
  "source_id": "과방위_20240611_52074_52074",
  "committee": "과학기술정보방송통신위원회",
  "folder": "과방위",
  "file_name": "20240611_52074_52074.pdf",
  "date_hint": "20240611",
  "page": 1,
  "text": "페이지 원문..."
}
```

- 이미 처리된 source_id는 스킵 (멱등성)
- 전체 페이지 빈 텍스트 PDF는 오류 로그 후 건너뜀

---

### normalizer_v1.py (NORMALIZER_VERSION = v1.0)

페이지 텍스트를 정리하고 문서 구간을 분류한다.

출력: `data/v1/normalized/{source_id}/normalized.jsonl`

**처리 내용:**
- 반복 헤더 / 페이지 번호 제거
- 한국어 줄바꿈 복원 (마지막 토큰 길이 ≤3 → 붙이기, 동사 연속 형태소 강제 붙이기)
- `section_type` 분류: `cover` / `agenda` / `body` / `report` / `mixed` / `unknown`
- `segments` 배열 생성 (페이지 내 구간 분리)
- `has_speaker_marker` 계산 (body 세그먼트 기준으로만)

---

### parser_v1.py (PARSER_VERSION = v1.2)

발언자 마커를 기준으로 speaker turn을 구조화한다.

출력: `data/v1/parsed/{source_id}/turns.jsonl`

**마커:** `◯` (전 위원회 공통) + `◎` (정무위 일부 사용)

**핵심 규칙:**
- body 세그먼트만 파싱 (cover/agenda/report/attendance 제외)
- 페이지 경계 continuation 처리: 다음 페이지 body 앞 cover 세그먼트가 마커 없이 시작하면 직전 turn에 이어붙임
- 비발언 항목 제외: 출석 위원, 정부측 참석자, 의안 회부, 보고사항, 출장 위원 통계, 소위원회 회부 등

**발언자 추출 패턴 (적용 순서):**

| 순서 | 패턴 | 예시 |
|------|------|------|
| 1 | 부처+직책 선행 | `◯외교부장관 조태열` → name=조태열, role=외교부장관 |
| 2 | 역할 선행 (위원장류) | `◯위원장 최민희` → name=최민희, role=위원장 |
| 3 | 역할 선행 (일반형, v1.1~v1.2) | `◯증인 유영상`, `◯한국방송공사사장후보자 박장범`, `◯보건복지부제2차관 이형훈`, `◯쿠팡㈜대표이사 박대준` |
| 4 | 이름 선행 | `◯김현 위원`, `◯柳榮夏 위원`(한자) → role=위원 |
| 5 | 이름만 | `◯홍길동` → name=홍길동, role=None |

**버전 이력:**

| 버전 | 변경 내용 |
|------|-----------|
| v1.0 | 최초 구현. 패턴 1·2·4·5만 지원 → `증인/전문위원/각종 청장·차장·위원장 + 이름` 헤더에서 직함을 이름으로 오인(31,258청크), 인식 불가 헤더의 발언 27,091턴을 조용히 폐기하는 문제 |
| v1.1 | `ROLE_FIRST_GENERAL_RE` 일반화 정규식 추가(패턴 3). 이름부는 한국명 + 외국인 음차명(`해럴드로저스`) + 익명처리(`박00`) 지원. 비발언 잡음(`출장 위원(N인)` 등) 차단. 직함 오인 31,258→2청크 |
| v1.2 | 버려진 헤더 리포트(`parser_dropped_headers.txt`) 도입 — "조용한 폐기" 방지. 리포트 기반으로 접미사 40여 종 확장(후보자/직무대행/사령관/총재/과장/감사 등), 직함 내 숫자(`제2차관`)·특수문자(`쿠팡㈜`,`(전)`)·한자명(`柳榮夏`, 호환용 한자 U+F900 블록) 지원, `청가` 잡음 차단. 버려진 헤더 59,550→1,001건(-98%), 최종 418,758턴/419,882청크 |

**단위 테스트:** `tests/test_parser_speaker.py` — 실측 헤더 50케이스 / `tests/test_quality_gates.py` — 게이트 자체 검증 4케이스

```json
{
  "source_id": "과방위_20240611_52074_52074",
  "committee": "과학기술정보방송통신위원회",
  "meeting_date": "2024-06-11",
  "turn_id": "과방위_20240611_52074_52074_turn_0002",
  "speaker": "김현",
  "role": "위원",
  "header_raw": "김현 위원 안녕하십니까?",
  "text": "안녕하십니까?...",
  "page": 1,
  "page_start": 1,
  "page_end": 2,
  "source_segment_type": "body",
  "source_segment_index": 2,
  "parser_version": "v1.2"
}
```

---

### turns_quality_gate.py

파싱 결과를 검사한다. BLOCK 발생 시 종료 코드 1.

```bash
python scripts/turns_quality_gate.py              # 전체
python scripts/turns_quality_gate.py 과방위 외통위  # 위원회 필터
python scripts/turns_quality_gate.py --source 과방위_20240611_52074_52074
```

| 항목 | 기준 | 레벨 |
|------|------|------|
| speaker 누락률 | 1% 이상 | BLOCK |
| meeting_date 형식 오류 | 1건 이상 | BLOCK |
| turn_id 중복 | 1건 이상 | BLOCK |
| page_start > page_end | 1건 이상 | BLOCK |
| 비발언 항목 speaker 잔존 | 1건 이상 | BLOCK |
| 필수 메타데이터 누락 | 1건 이상 | BLOCK |
| ◯ 마커 text 잔존 | 발생 시 | WARNING |
| 20자 미만 turn 비율 | 20% 이상 | WARNING |

리포트: `data/v1/reports/turns_quality/{source_id}/turns_quality_report.json`

---

### policy_enricher_v1.py (ENRICHER_VERSION = v1.0)

발언 turn에 정책 도메인 메타데이터를 추가한다. (rule-based)

출력: `data/v1/enriched/{source_id}/enriched_turns.jsonl`

| 필드 | 방식 |
|------|------|
| `policy_domain` | 위원회명 → 정책 분야 매핑 |
| `bill_refs` | 법안명 패턴 매칭 (개정안/기본법/특별법 등) |
| `utterance_type` | question / statement / motion |
| `stance_signals` | positive / negative / neutral / mixed |
| `mentions` | 부처·기관명 추출 (과기부, 방통위 등 29개) |

v2에서 LLM 기반 분류로 고도화 예정.

---

### chunker_v1.py (CHUNKER_VERSION = v1.0)

enriched turn을 RAG 검색에 적합한 chunk로 분할한다.

출력: `data/v1/chunks/{source_id}/chunks_v1.jsonl`

**분할 규칙:**
- 2,500자 초과 → 문장 단위 분할
- 그 외 → turn 1개 = chunk 1개

**핵심 필드:**

```json
{
  "chunk_id": "과방위_20240611_52074_52074_turn_0002_chunk_001",
  "turn_id": "과방위_20240611_52074_52074_turn_0002",
  "chunk_type": "utterance",
  "source_id": "...",
  "committee": "과학기술정보방송통신위원회",
  "meeting_date": "2024-06-11",
  "speaker": "김현",
  "role": "위원",
  "page_start": 1,
  "page_end": 2,
  "text": "...",
  "context_before": "최민희 위원장: 다음은...",
  "context_after": "최민희 위원장: 다음은 김우영...",
  "embed_text": "과학기술정보방송통신위원회 2024-06-11 김현 위원 발언: ...",
  "is_short": false,
  "policy_domain": "과학기술/방송통신/ICT",
  "bill_refs": [],
  "utterance_type": "statement",
  "stance_signals": "positive",
  "mentions": [],
  "parser_version": "v1.2",
  "chunker_version": "v1.0"
}
```

---

### chunks_quality_gate.py

청크 품질을 검사한다.

```bash
python scripts/chunks_quality_gate.py data/v1/chunks/{source_id}/chunks_v1.jsonl
python scripts/chunks_quality_gate.py --all
```

| 항목 | 기준 | 레벨 |
|------|------|------|
| meeting_date null 비율 | 5% 이상 | BLOCK |
| speaker 누락 비율 | 10% 이상 | BLOCK |
| committee 누락 | 1건 이상 | BLOCK |
| 빈 text 비율 | 5% 이상 | BLOCK |
| chunk_id 중복 | 1건 이상 | BLOCK |
| chunk_type 오류 | 1건 이상 | BLOCK |
| source 추적 불가 비율 | 1% 이상 | BLOCK |
| 100자 미만 chunk 비율 | 20% 이상 | WARNING |
| 3,000자 초과 chunk 비율 | 5% 이상 | WARNING |

> 참고: `--all` 경로가 `data/v2/chunks` 오타로 되어 있어 실제로는 아무것도 검사하지
> 않던 버그를 2026-07-02 수정 (`data/v1/chunks`). 수정 후 767/767 실검사 통과.

---

### jsonl_to_postgres.py (✅ 구현 완료, 2026-07-02)

chunks_v1.jsonl을 PostgreSQL에 적재한다.

```bash
python scripts/jsonl_to_postgres.py              # 전체
python scripts/jsonl_to_postgres.py 과방위 외통위  # 특정 위원회만
```

- 환경 변수: `DATABASE_URL` (`.env`에서 로드, 예: `postgresql://user:pass@localhost:5432/national_assembly`)
- 스키마: `db/schema.sql` 자동 실행 (`IF NOT EXISTS` — 반복 안전)
  - 정규화 5테이블: `committees` / `meetings` / `speakers` / `chunks` / `embeddings_openai`
  - `vector(1536)` 컬럼은 pgvector 확장 필요 (`CREATE EXTENSION vector`)
- 적재 방식:
  - source(회의)별 `committees`/`meetings` upsert → committee_id 확보
  - `chunks`는 source_id 기준 **DELETE 후 재삽입** (재실행 안전), `execute_values` 대량 삽입
  - 적재 직후 행 수 검증 (JSONL 줄 수 == DB 행 수, 불일치 시 롤백)
  - 인라인 품질 체크: meeting_date 결측 50% 초과 / 빈 본문 20% 초과 source는 skip
  - 종료 후 `speakers`를 chunks에서 집계로 재생성 (name, role, committee_id, utterance_count)
- 적재 결과 (2026-07-02): 767/767 source, 419,882청크, 고아 청크 0, 건너뜀·불일치 0
- 의도적 비정규화: chunks에 `committee_id`/`meeting_date` 중복 보관 → 검색 필터 최적화

---

### embeddings_v1.py (✅ 구현 완료, 2026-07-02)

chunks.embed_text 를 OpenAI 임베딩으로 변환해 `embeddings_openai`(pgvector)에 저장한다.

```bash
python scripts/embeddings_v1.py --dry-run     # 대상 수·예상 비용만 확인 (과금 없음)
python scripts/embeddings_v1.py --limit 1000  # 테스트: 처음 N개만
python scripts/embeddings_v1.py 기재위         # source_id 접두사 필터
python scripts/embeddings_v1.py               # 전체 (미임베딩 청크만)
```

- 환경 변수: `OPENAI_API_KEY`, `DATABASE_URL`
- 모델: `text-embedding-3-small` (1536차원)
- 증분 처리: 이미 임베딩된 chunk_id 스킵, 배치 단위 커밋 → 중단 후 재실행 안전
- 배치: 요청당 최대 800텍스트/12만 자, rate limit 은 지수 백오프 재시도
- 실행 결과 (v1.2 최종): 419,882청크 / 약 $1.26 (v1.1 1차분 $1.1 별도 — 총 ~$2.4)
- HNSW 인덱스: `idx_embeddings_openai_hnsw` (cosine), 검색 16~64ms
  - Windows 병렬 빌드 공유 메모리 초과 시 `SET max_parallel_maintenance_workers = 0`
  - 대량 재적재 시 인덱스 DROP → 임베딩 → 재생성 순서가 빠름
- 검증 발견: 주제형 질문은 벡터 검색 정확, 고유명사형(법안명·사건명·인물명)은 벡터 단독 부정확
  → 2단계 하이브리드 검색(벡터+키워드) 필요성의 실측 근거
- 재임베딩 표준 절차(인수인계): `claude.txt` — HNSW DROP → 재적재 → --limit 테스트 → 전체 → 인덱스 재생성

---

### run_pipeline.py (실행기)

파일 파이프라인을 순서대로 실행한다. 게이트 실패 시 그 자리에서 중단 — 손 순서 실수 방지.

```bash
python scripts/run_pipeline.py --from parse --clean   # parse부터 재생성 (산출물 삭제 후)
python scripts/run_pipeline.py --from chunks_gate     # 게이트만 재실행
```

- `--clean` 은 해당 단계부터 하류 파생 산출물만 삭제. extract 층은 `--clean-extract` 명시 필요
- DB 적재·임베딩은 비용이 들어 포함하지 않음 (종료 시 다음 명령 안내)

---

### etl_audit.py (원본 대조 감사)

DB의 청크를 무작위 샘플링해 원본 추출 텍스트(extract 층)와 대조한다.

```bash
python scripts/etl_audit.py 300    # 무작위 300개 감사
```

- 검사: 본문 probe(20자×5지점) 존재 + 발언자 이름 존재 + 페이지 범위 정확성
- 페이지 경계를 걸치는 발언은 접두사/접미사 분할 검사로 재확인 (원본의 페이지 헤더 개입 대응)
- **최종 결과 (2026-07-02): 300/300 (100%) 통과** — 본문·발언자·페이지 모두 원본과 일치
- 실적: 이 감사가 `청가` 잡음 425청크를 발견 → 파서 v1.2 개선의 출발점

---

### retrieval_eval.py (✅ 구현 완료, 2026-07-02 — RAG-5)

검색 품질을 정답셋으로 채점한다. 상세 설계·기준선은 `docs/progress.md` 의 "RAG-5 구현 기록" 참조.

```bash
python scripts/retrieval_eval.py            # keyword/vector/hybrid 3모드
python scripts/retrieval_eval.py hybrid     # 하이브리드만 (~2분)
```

- 평가셋: `data/eval/retrieval_eval_set.json` — 63문항 13유형 (criteria 방식, ID 재배열에도 유효)
- 지표: Recall@5/@10, MRR@10, unanswerable 반전 채점
- **기준선 (2026-07-02)**: hybrid Recall@5=0.949 / MRR=0.885 / unanswerable 4/4
- 원칙: 검색·가중치·프롬프트 변경 시마다 재실행해 퇴행 감지 (마스터 5-5)

---

### pipeline_report.py

전 단계 산출물 현황을 집계한다.

```bash
python scripts/pipeline_report.py
```

출력 예 (2026-07-02, parser v1.2 기준):
```
[extract]    767개 source  /    41,571페이지
[normalize]  767개 source  /    41,571페이지
[parse]      767개 source  /   418,758턴
[enrich]     767개 source  /   418,758턴
[chunk]      767개 source  /   419,882청크
```

---

## 공통 메타데이터 필드

모든 단계의 산출물에 포함되는 필드:

```
source_id      — 파일 식별자 (폴더명_파일명스템)
committee      — 위원회 전체명
folder         — 위원회 약칭 (과방위 등)
file_name      — 원본 PDF 파일명
meeting_date   — YYYY-MM-DD (parse 단계부터)
```

---

## 보안 원칙

- `OPENAI_API_KEY`, `DATABASE_URL` 등 비밀값은 절대 코드에 포함하지 않는다
- 모든 비밀값은 `.env`에서 `os.environ`으로 읽는다
- `.env`는 `.gitignore`에 등록됨 — GitHub에 올리지 않는다
