# 국회 회의록 RAG 서비스

국회 회의록을 근거로 정책 의제, 행위자, 쟁점, 입장 차이, 시계열 흐름을 분석하는 GovTech RAG 서비스.

> **현재 상태 (2026-07-11):** **2단계 완료 + 3단계(정치분석) 사실상 완료 — POL-9 대시보드 확장만 잔여.**
> 질문 → 하이브리드 검색(R@5 0.983 [※측정 한계](#알려진-한계)) → GPT-4o-mini 답변(`[n]` 인용) → Grounding 신뢰등급(FULL/PARTIAL/REFUSED/NONE) → 원문 확인에 더해:
> - **쟁점 분석 탭**: 24개 쟁점 사전 × 월별 타임라인 차트 × 행위자 입장 매트릭스 × **여야 구도**(정당별 입장 분포 + 정권교체 시점성 배지, POL-6)
> - **report 브리핑 이슈 분석 자동 주입**(POL-8): 질문에서 쟁점 감지 → 구도·발언 피크·주요 행위자를 "(코퍼스 분석 기준)"으로 개요에 반영
> - **입장 판정 eval 자산**(POL-7): 블라인드 라벨 도구 + LLM 교차 기준선 67.5% (재실행 가능 — 사람 검증은 잔여)
>
> 상세 진행 기록은 `docs/progress.md` 참조.

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
>
> ⚠️ 이 수치는 **상향 편향**되어 있다. 정답 판정이 "검색 결과 본문에 질문 키워드가
> 포함되는가"인데 키워드 검색 축이 같은 연산을 수행한다 — 검색기가 채점 기준을
> 미리 아는 구조다. 63문항 중 6문항(`r043`~`r047`·`r062`)은 내용 판정 기준 자체가
> 없어 필터만 맞으면 전원 정답 처리된다. 등급 라벨(0/1/2) 기반 재측정 진행 중 —
> 상세는 [알려진 한계](#알려진-한계).

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

## 공개 배포 (4단계-B 런북)

무료 3-스택: **Vercel**(프론트) + **Render free**(백엔드) + **Supabase free**(DB).
배포 코퍼스는 이슈 중심 축소본 — `scripts/make_deploy_corpus.py` 가 생성 (전체 9.6GB
중 이슈 관련 turn만 ≤350MB, 검색 eval 수치 R@5 0.983 은 전체 코퍼스 로컬 측정 기준 —
[측정 한계](#알려진-한계) 참조).

### 순서 (사용자 체크리스트)

1. **Supabase**: 프로젝트 생성 (리전 Northeast Asia) → Settings > Database 의
   Connection string(URI) 복사 → 로컬 `.env` 에 `DEPLOY_DATABASE_URL=...` 추가
   - **Session Pooler**(포트 5432) URI 를 쓸 것 — Direct connection 은 IPv6 전용이라
     로컬·Render 에서 접속 실패할 수 있음. Transaction Pooler(6543)는 스키마 작업에 부적합.
2. **코퍼스 이전** (로컬에서): `python scripts/make_deploy_corpus.py` — 행수 검증
   리포트 `[OK]` 확인 (dry-run 먼저: `--dry-run`)
   - `db/indexes.sql` 은 원격에 실행하지 말 것 — trgm 인덱스는 축소본에 의도적으로
     미생성(용량 절약), HNSW 는 스크립트가 한도 내일 때 직접 생성한다.
3. **Render**: New Web Service → GitHub 저장소 연결 → Root Directory `backend`,
   Build `pip install -r requirements.txt`, Start
   `uvicorn main:app --host 0.0.0.0 --port $PORT`, Health Check Path `/health`
   - 환경변수: `DATABASE_URL`(Supabase URI), `OPENAI_API_KEY`,
     `BACKEND_CORS_ORIGINS`(Vercel 도메인, 배포 후 갱신 — 형식은
     `https://<app>.vercel.app`, 끝 슬래시 없음), `RERANKER_ENABLED=1`,
     `PYTHON_VERSION=3.12.10`
   - `JWT_SECRET`: 회원 토큰 서명 키 — 임의의 긴 문자열 (예: `python -c "import secrets; print(secrets.token_hex(32))"`)
4. **Vercel**: Add New Project → 같은 저장소 → Root Directory `frontend` →
   환경변수 `VITE_API_URL`(Render URL) → Deploy → 도메인을 Render 의
   `BACKEND_CORS_ORIGINS` 에 반영(재배포)
5. **스모크 6항목**: `/health` 200(행수=축소본) / report 질의 1건(issue_context 포함)
   / 쟁점 탭 24개 이슈 / 프로필 김윤 / 연속 6회 질의 → 429 / 15분 방치 후 콜드스타트 배너

### 운영 방어선 (기본값)

IP당 LLM 분당 5회·일반 60회, 일별 OpenAI 비용 상한 $1 (초과 시 한국어 안내).
상세: `docs/superpowers/specs/2026-07-11-dep-a-guardrails-design.md`

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
- [x] **3단계: 정책 도메인 분석 기능 — 완료 (2026-07-11, POL-0~9 전 항목)**
  - [x] POL-0 정당 모듈(시점별 여야 판정·role 게이트) → [x] POL-1 enrichment 실태 조사(입장은 LLM 판정 확정)
  - → [x] POL-2 행위자 프로필 API → [x] POL-3 쟁점 사전 24개 → [x] POL-4 타임라인
  - → [x] POL-5 입장 분석(3,270 판정 🔶 사람 기준선 27.5% — rubric 재정렬 후속) → [x] POL-6 여야 구도
  - → [x] POL-7 입장 eval 도구 → [x] POL-8 브리핑 분석 주입 → [x] POL-9 의원 프로필·양방향 대시보드
- [x] 코드 전수 검토 + 1차 수정 (2026-07-06): 34건 도출(검토 32 + 평가 보고서 2), 12건 완료 —
  재적재 임베딩 유실 방지, 긴 발언 맥락 복원, 근거 블록 로그, 입력 검증, 테스트 pytest 정합화 등
  (`docs/fix_checklist.md`, 평가는 `docs/llm_comparison_report.md`)
- [ ] 4단계: GovTech 배포 버전 — 진행 중 (A 방어선 ✅ rate limit·비용 상한 / B 배포 준비 ✅ 코드 완결 — 잔여: 계정 연결, README 런북 참조)

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

## 알려진 한계

측정으로 확인된 것만 적는다. 해결 예정 여부와 무관하게 공개한다 —
숫자를 인용할 때 이 절을 함께 읽어야 한다.

**1. 검색 평가 지표가 상향 편향돼 있다 (가장 중요)**
`retrieval_eval.py` 의 정답 판정은 "결과 본문에 질문 키워드가 포함되는가"인데,
키워드 검색 축이 정확히 같은 연산을 한다. 검색기가 채점 기준을 미리 아는
동어반복 구조라 R@5 0.983 은 실제 검색 품질이 아니라 "키워드를 잘 찾았다"에
가깝다. 63문항 중 6문항은 내용 판정 기준 자체가 없어 필터만 맞으면 전원 정답이다.
→ LLM 심판으로 등급 라벨(0/1/2) qrels 를 만들고 nDCG 를 추가하는 재측정 진행 중.
그 전까지 이 저장소의 검색 성능 수치는 **비교용 상대값으로만** 취급할 것.

**2. 검색 결과가 실행마다 미세하게 달라진다**
원인 2개 — ① 별칭 확장이 `set` 을 반환해 순서가 고정되지 않은 채 상위 8개로
잘린다 ② 키워드 검색의 `ORDER BY` 에 동점 처리 기준이 없어 동점(대부분 1점)
구간의 순서가 보장되지 않는다. 재측정의 선행 조건이라 함께 처리 중.

**3. 동시 처리 한계가 낮다**
DB 커넥션 풀 5개인데 질의 1건이 키워드·벡터 두 축을 병렬 실행하며 2개를 동시에
점유한다 → 실질 동시 처리 2~3건. 초과분은 최대 10초 대기 후 오류. 데모 규모
전제의 설정이며 수평 확장은 범위 밖.

**4. 정당 라벨은 '현재 당적' 스냅샷이다**
`members` 테이블의 최종 당적을 쓰므로 임기 중 탈당·합당·정당 개편을 추적하지
않는다. 과거 회의 발언에 현재 당적이 표시될 수 있다. 여야 판정은 발언 시점
기준이라 정확하지만, 정당명 자체는 시점 정확성을 보장하지 않는다.

**5. 답변 검증층은 메타데이터 정합성만 본다**
규칙 7종은 화자·정당·날짜·정권기 귀속의 형식 일치를 검사한다. "근거가 주장을
실제로 뒷받침하는가"(내용 함의)는 검사하지 않으며, 답변에 인용 번호가 하나도
없으면 검증 자체를 생략한다. 또한 규칙 예외는 63문항 평가셋의 실패 사례에서
파생돼 있어 새로운 질문 유형에서의 일반화는 검증되지 않았다.

**6. 배포본은 축소 코퍼스이며 인덱스 구성이 다르다**
공개 배포는 이슈 관련 turn 만 담은 ≤350MB 축소본이고, 용량 제약으로 trgm
인덱스를 생성하지 않는다. **로컬에서 측정한 검색 지연 수치는 배포 환경에
적용되지 않는다.** 프로필·타임라인 통계도 축소본 기준이다(화면에 명시).

**7. 운영 데이터가 부족하다**
실사용 질의 로그·평점 표본이 매우 작아 품질 주장의 근거로 쓸 수 없다.
현재의 모든 수치는 자체 평가셋 기반이다.

---

## 보안 원칙

- API 키, DB 비밀번호는 코드에 절대 포함하지 않는다
- `.env`는 GitHub에 올리지 않는다 (`.gitignore` 등록됨)
- `.env.example`만 커밋한다
