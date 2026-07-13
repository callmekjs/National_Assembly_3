# 4단계-B 배포 실행 — 이슈 중심 축소 코퍼스 + 무료 3-스택 (spec)

> 2026-07-11(자정 무렵) 브레인스토밍 확정. 목표: **공개 URL 포트폴리오** — 월 $0~5.
> 스택: Vercel(프론트) + Render free(백엔드) + Supabase free(DB, pgvector).
> 4-A 방어선(rate limit·비용 상한) 병합 완료가 전제 — 이 스펙은 그 위의 실행편.

## 실측 (2026-07-11, 설계 근거)

- 로컬 DB 전체 **9.6GB** — embeddings_openai 8.6GB(HNSW 포함, ~21KB/행), chunks 935MB(~2.3KB/행). 무료 한도 500MB 에 위원회 1개도 인덱스째 불가.
- **이슈 매핑 청크 6,557개** (24개 이슈, 9개 위원회에 분산 — 위원회 컷은 이슈를 깨고,
  이슈 컷은 전부 살림). 위원회별 이슈 커버리지: 과방위 20/정무위 19/행안위 18….

## 사용자 결정 (2026-07-11)

1. **이슈 중심 축소본** — 이슈 매핑 청크가 속한 turn 전체 + 같은 회의 인접 ±1 turn.
   목표 **≤350MB**(무료 500MB 의 70%), 초과 시 인접 turn 부터 제외. 복지위 단독·유료 DB 기각.
2. 콜드스타트 UX: 프론트 로드 시 `/health` ping → 미기동이면 "무료 서버를 깨우는
   중입니다(~1분)" 배너.
3. 정직 표기: 푸터에 "데모 코퍼스: 24개 쟁점 관련 발언 부분집합 — 전체 42만 청크는
   로컬 데모" (프로필 통계도 부분집합 기준).
4. 리랭커 **켬**(RERANKER_ENABLED=1) — 품질 스토리 유지, 비용 상한이 방어.
5. 계정 게이트(사용자 작업): Supabase 프로젝트 생성(리전 가까운 곳, DATABASE_URL 복사),
   Render·Vercel GitHub 연결 가입. 실행 중 단계별 안내.

## 구성 1 — 축소 코퍼스 생성·이전 (`scripts/make_deploy_corpus.py` 신규)

로컬 → 원격(Supabase) 직접 복사. 덤프 파일 경유 없음(임베딩 텍스트 직렬화로 충분).

- **대상 산출 (로컬)**:
  1. core turn: `SELECT DISTINCT c.turn_id FROM issue_chunks ic JOIN chunks c USING (chunk_id)`
  2. 인접 turn: 같은 source_id 에서 turn 순번 ±1 (answer.py `neighbor_turn_ids` 와 동일
     규칙 — `{src}_turn_{n±1}`)
  3. 대상 chunk = 위 turn 들의 모든 청크. **사이즈 추정 출력**(행수 × 실측 행단가) 후
     350MB 초과면 인접 turn 제외하고 재산출 (`--no-neighbors` 자동 폴백, 로그 명시).
- **이전 순서 (원격)**: 스키마 적용(`db/schema.sql` — pgvector extension 은 Supabase
  대시보드/SQL 로 활성) → 소형 테이블 전량 복사(committees, meetings, speakers,
  members, issues, issue_chunks, issue_stances — issue_chunks 는 대상 chunk 존재분만,
  FK 정합) → chunks 부분 복사 → embeddings 부분 복사 → **HNSW 인덱스 생성**(2만 행
  수준이라 분 단위) → 행수 검증 리포트(로컬 대상 수 == 원격 적재 수).
- 멱등: 테이블별 TRUNCATE 후 재적재 (`--wipe-remote` 명시 플래그, 기본은 빈 DB 전제).
- 원격 접속: `DEPLOY_DATABASE_URL` 환경변수 (로컬 `.env` 에만, 커밋 금지).
- query_logs 는 빈 상태로 시작(스키마만) — 비용 상한 집계가 0 부터.

## 구성 2 — 백엔드 배포 (Render free)

- `runtime.txt`(또는 render 설정)로 **Python 3.12 핀** (PEP604 문법 — 4-A 리뷰 지적).
- `requirements.txt` 확인: backend 실행 의존성이 전부 있는지 (fastapi, uvicorn,
  psycopg2-binary, openai, pydantic, python-dotenv 등) — scripts/requirements.txt 와
  분리돼 있으면 배포용 정리.
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT` (working dir backend/).
- 환경변수: `DATABASE_URL`(Supabase), `OPENAI_API_KEY`, `BACKEND_CORS_ORIGINS`
  (Vercel 도메인), `RERANKER_ENABLED=1`, 방어선 3종은 기본값 사용(미설정).
- `/health` 를 Render 헬스체크 경로로.

## 구성 3 — 프론트 배포 (Vercel) + 콜드스타트 UX

- Vercel: root `frontend/`, 빌드 `npm run build`, env `VITE_API_URL`(Render URL).
- **콜드스타트 배너**: App.jsx 마운트 시 `/health` fetch(타임아웃 90초) — 응답 전까지
  상단 배너 "무료 서버를 깨우는 중입니다 (최대 1분)…", 응답 오면 제거. 실패하면
  "서버 연결 실패 — 잠시 후 새로고침" 으로 전환. api.js 의 기존 DEFAULT_TIMEOUT(20초)은
  깨어난 뒤 기준이라 유지.
- **푸터 정직 표기** (문구 verbatim):
  `데모 코퍼스: 24개 쟁점 관련 발언 부분집합 (전체 42만 청크는 로컬 데모) — 의원 프로필 통계도 이 부분집합 기준입니다.`

## 검증 (배포 후 스모크 — 완료 기준)

1. `/health` 200 (chunks·embeddings 행수가 축소본 수치와 일치)
2. 실질의 1건: "의대 정원 증원 논의 정리해줘" (report) → 답변 + `issue_context` + 인용
3. 쟁점 분석 탭: 이슈 드롭다운 24개, medical-reform 타임라인·구도·매트릭스 렌더
4. 의원 프로필: 김윤 조회 → 이슈별 입장 렌더
5. 방어선: 연속 6회 질의 → 6번째 429 (한국어 detail, CORS 헤더)
6. 콜드스타트: 15분+ 방치 후 접속 → 배너 표시 → 깨어나면 정상

## 정직한 처리 (문서화)

- 일반 검색은 이슈 관련 내용만 히트 — 푸터 표기 + README 배포 섹션에 명시.
- 프로필 통계(발언 수·위원회 분포)는 부분집합 기준 — 푸터 문구가 커버.
- Render free 콜드스타트 ~1분, 월 750시간 한도(단일 서비스면 충분).
- retrieval eval 수치(0.983)는 전체 코퍼스 기준 — 배포본 재측정은 범위 밖(README 에
  "수치는 전체 코퍼스 로컬 측정" 각주).

## 범위 밖 (후속)

커스텀 도메인, 배포본 eval 재측정, 신규 회의록 증분 인입, 모니터링·알림, /answer
엔드포인트 정리, rubric 재정렬 재라벨(별도 트랙).
