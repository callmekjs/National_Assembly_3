# 국회 회의록 RAG 서비스

국회 회의록을 근거로 정책 의제, 행위자, 쟁점, 입장 차이, 시계열 흐름을 분석하는 GovTech RAG 서비스.

> **현재 상태 (2026-07-03):** **2단계 완료** — 브라우저에서 질문 → 하이브리드 검색 →
> GPT-4o-mini 답변(`[n]` 인용) → Grounding 신뢰등급(FULL/PARTIAL/REFUSED/NONE) → 출처 클릭
> → 원문 확인까지 동작. 3단계 진행 중 (1차 **정당 모듈**: 발언 시점 기준 여야 판정 완료)

---

## 프로젝트 구조

```
National_Assembly_3/
├── incoming_data/        # PDF 원본 (767개, 9개 위원회)
├── data/v1/
│   ├── extract/          # pages.jsonl (767개, 41,571페이지)
│   ├── normalized/       # normalized.jsonl (767개)
│   ├── parsed/           # turns.jsonl (767개, 418,758턴)
│   ├── enriched/         # enriched_turns.jsonl (767개)
│   ├── chunks/           # chunks_v1.jsonl (767개, 419,882청크)
│   ├── eval/             # 검색 평가셋 63문항 + prototype 75문항 원본
│   └── reports/          # quality gate 리포트, retrieval eval 리포트
├── data/members/         # 22대 의원-정당 매핑 + 한자 별칭 (정당 모듈)
├── db/
│   └── schema.sql        # PostgreSQL 스키마 (정규화 5테이블 + pgvector)
├── backend/              # FastAPI 백엔드
├── frontend/             # React + Vite 프론트엔드
├── scripts/              # ETL 파이프라인 스크립트
└── docs/                 # 설계 문서
```

---

## 데이터 파이프라인 실행

```bash
python scripts/manifest_builder.py       # PDF 목록 스캔
python scripts/extractor_v1.py           # PDF → 페이지 텍스트
python scripts/normalizer_v1.py          # 잡음 제거 + 섹션 분류
python scripts/parser_v1.py             # 발언자 턴 파싱
python scripts/turns_quality_gate.py    # 파싱 품질 검사
python scripts/policy_enricher_v1.py    # 정책 도메인 메타데이터 추가
python scripts/chunker_v1.py            # RAG 청크 생성
python scripts/chunks_quality_gate.py --all  # 청크 품질 검사
python scripts/pipeline_report.py       # 전체 현황 리포트
python scripts/jsonl_to_postgres.py     # chunks → PostgreSQL 적재 (ETL-7)
python scripts/embeddings_v1.py         # OpenAI 임베딩 → pgvector (ETL-8, --dry-run/--limit 지원)
python scripts/etl_audit.py 300         # 무작위 청크 ↔ 원본 대조 감사
python scripts/retrieval_eval.py        # 검색 품질 평가 (Recall@k, MRR — 63문항)
python scripts/build_members.py         # 22대 의원-정당 매핑 수집·적재 (정당 모듈, OPEN_ASSEMBLY 키 필요)
```

> **검색 기준선 (2026-07-02)**: hybrid Recall@5 = 0.949 / MRR@10 = 0.885 / unanswerable 4/4

한 번에 실행 (파일 파이프라인, 게이트 실패 시 자동 중단):
```bash
python scripts/run_pipeline.py --from parse --clean   # parse부터 재생성
```

> ETL-7 은 `.env` 의 `DATABASE_URL` 로 접속하며, `db/schema.sql` 을 자동 실행해
> 스키마(committees/meetings/speakers/chunks/embeddings_openai)를 보장한다.

특정 위원회만 처리:
```bash
python scripts/extractor_v1.py 과방위 외통위
```

---

## 서비스 실행 방법

### 백엔드

```bash
# 선행: Docker Desktop 실행 (PostgreSQL 컨테이너 national-assembly-db 가 자동 시작됨)
cd backend
python -m venv .venv
.venv\Scripts\activate       # Windows
pip install -r requirements.txt
cp ../.env.example ../.env   # .env 파일에 실제 값 입력
python -m uvicorn main:app --port 8000   # → http://127.0.0.1:8000
```

> `--reload` 는 Windows 에서 hang 되므로 사용하지 않는다 (코드 수정 시 수동 재시작)

### 프론트엔드

```bash
cd frontend
npm install
npm run dev    # → http://localhost:5173
```

---

## API 엔드포인트

| 메서드 | 경로 | 설명 | 상태 |
|--------|------|------|------|
| GET | `/health` | 서버·DB 상태 + 행수 확인 | ✅ |
| GET | `/committees` | 위원회 목록 (+회의 수) | ✅ |
| GET | `/meetings` | 회의 목록 (위원회·기간 필터) | ✅ |
| GET | `/speakers` | 발언자 목록 (발언 수 순, 이름 검색) | ✅ |
| GET | `/citations/{chunk_id}` | 원문 발언 + 맥락 + PDF 페이지 | ✅ |
| GET | `/search/keyword` | 키워드 검색 (pg_trgm + 별칭 사전) | ✅ |
| GET | `/search/vector` | 벡터(의미) 검색 (HNSW + 필터) | ✅ |
| GET | `/search/hybrid` | 하이브리드 검색 (RRF 융합) | ✅ |
| GET | `/actors/{name}` | 행위자 프로필 — 발언 통계·여야 이력·주요 언급 기관 | ✅ |
| POST | `/answer` | 답변 생성 (qa/report 모드 — 디버그용 원시 호출) | ✅ |
| POST | `/query` | RAG 통합: 답변 + 출처 + Grounding 등급 + query_logs | ✅ |
| POST | `/feedback` | 답변 평가 (query_id 로 rating 저장) | ✅ |

> API 문서: 서버 실행 후 http://127.0.0.1:8000/docs
> (Windows 에서는 `localhost` 대신 `127.0.0.1` 사용 — IPv6 우선 시도로 2초+ 지연됨)

---

## 개발 마일스톤

- [x] 1단계: 프로젝트 골격 (FastAPI 스텁 + React UI)
- [x] ETL-0: PDF 수집 (767개, 9개 위원회)
- [x] ETL-1: PDF 텍스트 추출 (41,571페이지)
- [x] ETL-2: 텍스트 정규화 + 섹션 분류
- [x] ETL-3: 발언자 턴 파싱 (418,758턴)
- [x] ETL-4: turns quality gate (767/767 PASS)
- [x] ETL-5: 정책 도메인 enrichment (policy_enricher_v1)
- [x] ETL-6: RAG 청크 생성 (419,882청크)
- [x] ETL-7: PostgreSQL 적재 (9 committees / 767 meetings / 2,292 speakers / 419,882 chunks)
- [x] ETL-8: OpenAI 임베딩 생성 (419,882벡터, text-embedding-3-small + HNSW)
- [x] **2단계: RAG 검색 + 답변 생성 + 출처 표시 — 완료 (2026-07-03, 완료 기준 4/4)**
  - [x] RAG-0 기반 정비 → [x] RAG-1 조회 API → [x] RAG-2 키워드 검색 → [x] RAG-3 벡터 검색
  - → [x] RAG-4 하이브리드 → [x] RAG-5 검색 평가 → [x] RAG-6 답변 생성(qa/report 모드)
  - → [x] RAG-7 /query 통합(Grounding 판정 + query_logs) → [x] RAG-8 프론트(출처 패널·원문 모달)
- [ ] 3단계: 정책 도메인 분석 기능 (쟁점/시계열/행위자) — 진행 중, 세부 로드맵 POL-0~9 (`docs/progress.md`)
  - [x] POL-0 정당 모듈: 22대 의원-정당 매핑 + **발언 시점 기준 여야 판정** + 발언 자격(role) 게이트
    (국회의원만 정당 라벨, 행정부는 "정부측", 후보자·증인은 무표기 — `docs/party_module_spec.md`)
  - [x] POL-1 enrichment 실태 조사: ETL-5 필드 감사 — stance_signals·bill_refs·policy_domain
    사용 불가 판정, **입장 분석은 LLM 판정으로 확정** (분석을 불량 재료 위에 쌓기 전에 차단)
  - [x] POL-2 행위자 프로필 API: `/actors/{name}` — 발언 통계·여야 이력·주요 언급 기관·최근 발언
  - [ ] POL-3~9: 쟁점 사전 → 타임라인 → 입장 분석 → 여야 구도 → eval → 통합 → 프론트
- [x] 코드 전수 검토 + 1차 수정 (2026-07-06): 30건 도출, 13건 완료 — 재적재 임베딩 유실 방지,
  긴 발언 맥락 복원, 근거 블록 로그, 입력 검증, 테스트 pytest 정합화 등 (`docs/fix_checklist.md`)
- [ ] 4단계: GovTech 배포 버전

---

## 기술 스택

| 영역 | 선택 |
|------|------|
| 프론트엔드 | React + Vite |
| 백엔드 | FastAPI (Python) |
| 데이터베이스 | PostgreSQL + pgvector 0.8.1 (HNSW) |
| 임베딩 | OpenAI text-embedding-3-small |
| 답변 생성 | GPT-4o-mini — qa/report 모드, `[n]` 인용, Grounding 신뢰등급 |
| 의원 데이터 | 열린국회정보 Open API (22대 의원-정당 매핑) |
| 배포 | Vercel (FE) + Render/Fly.io (BE) + Supabase (DB) 예정 |

---

## 보안 원칙

- API 키, DB 비밀번호는 코드에 절대 포함하지 않는다
- `.env`는 GitHub에 올리지 않는다 (`.gitignore` 등록됨)
- `.env.example`만 커밋한다
