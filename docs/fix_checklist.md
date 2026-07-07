# 수정 체크리스트 (2026-07-06 코드 전수 검토 결과)

전체 코드 검토(Claude) + 외부 리뷰(친구) 지적을 통합한 목록.
위에서부터 순서대로 처리하는 것을 권장.
1~6순위 = 결함 수정 (검토에서 발견된 문제들) / 7순위 = A+ 로드맵 신규 항목
(결함이 아니라 상용 수준으로 가는 업그레이드 — `docs/llm_comparison_report.md` "A+ 로드맵").

## 1순위 — 데이터·비용·사용자 피해가 있는 버그

- [x] **재적재 시 임베딩 전체 삭제** — `scripts/jsonl_to_postgres.py` (2026-07-06 완료:
  DELETE 전 임시 테이블에 백업 → 재삽입 후 embed_text md5 가 같은 것만 복원.
  요약에 보존/유실 수 표시. 실 DB 검증: 4,092개 회의 재적재 후 전량 보존)
- [x] **한글 IME 조합 중 Enter 조기 제출** — `frontend/src/components/QueryForm.jsx` (2026-07-06 완료)
- [x] **pytest 에서 테스트가 무조건 통과** — tests 7개 파일 (2026-07-06 완료: check() assert 화,
  test_parser_speaker·test_quality_gates 는 pytest 수집 가능한 test_* 함수로 재구성,
  test_quality_gates 는 로컬 데이터 없으면 건너뜀. 덤: test_actors ↔ test_party 의
  _party_map 전역 공유 오염 발견·수정, test_party 한자 상수 이스케이프화. pytest 32건 통과)
- [x] **stdout 재래핑으로 pytest 러너 충돌** — tests 7개 + scripts 16개 (2026-07-06 완료:
  전부 `if __name__ == "__main__":` 가드로 — import 시 부작용 제거, 직접 실행은 동일)
- [x] **행위자 프로필 '주요 언급 기관' 뻥튀기** — `backend/actors.py` (2026-07-06 완료,
  (org, turn_id) DISTINCT 쌍을 집합 집계 — 별칭 병합 시 이중 카운트도 방지)

## 2순위 — 답변 품질·디버깅 (외부 리뷰 반영)

- [x] **검색된 발언의 나머지 조각 복원** — `backend/answer.py` (2026-07-06 완료:
  `_fetch_texts` 가 turn 전문 복원, 상한 4,000자 초과 시 검색 조각 중심 창 +
  경계 조각 부분 포함(… 표기). 단위 테스트 6건 추가, 실 DB 검증)
- [x] **LLM 에 들어간 근거 블록 로그 저장** — `backend/main.py` + `answer.py` + `db/schema.sql`
  (2026-07-06 완료: query_logs.source_block 컬럼 + ALTER 마이그레이션, API 응답에는 미노출.
  E2E 검증: 실질의 1건에서 10,591자 근거 블록 저장 확인. 답변 품질 평가 세트의 재료가 쌓이기 시작)
- [ ] **청킹 문장분할 보강** — `scripts/chunker_v1.py:35`
  `(?<=[.!?。])\s+` 는 구두점 뒤 공백이 필수라 "했습니다.그리고" 를 못 자름.
  공백 없는 경계 허용 + 구두점 전무 텍스트는 길이 기준 강제 분할(8,192토큰 초과 방지).

## 3순위 — 서버 안정성 (main.py / db.py)

- [x] **동시 6명부터 500** — `backend/db.py` (2026-07-07 완료: 풀 고갈 시 10초 한도
  재시도 대기 + 대여 시 SELECT 1 로 죽은 연결 폐기·교체. 동시 12요청 12/12,
  강제 절단 후 8/8 검증)
- [x] **OpenAI 임베딩 에러가 500 으로 샘** — `backend/main.py` (2026-07-06 완료, /query·/search/vector·/search/hybrid 모두 502 매핑)
- [x] **날짜 문자열 미검증** — `backend/main.py` (2026-07-06 완료, 전 엔드포인트 datetime.date 타입 → 잘못된 날짜 422 확인)
- [x] **rating 무제한** — `backend/main.py` (2026-07-06 완료, 1~5 제한 — 프론트 👍=5/👎=1 확인)
- [x] **question 길이 무제한** — `backend/main.py` (2026-07-06 완료, 2~1000자 + comment 2000자 제한)
- [x] **query_parser 월/일 범위 미검증** — `backend/query_parser.py` (2026-07-07 완료:
  실존하지 않는 날짜("13월"·"2월 30일"·ISO 오타)는 필터 미적용으로 일반 텍스트 취급,
  회귀 테스트 5건 추가)

## 4순위 — 재실행 안전성 (ETL)

- [x] **비원자적 쓰기 + 존재=완료 스킵** — 5개 스테이지 공통 (2026-07-07 완료:
  공용 모듈 `scripts/stage_io.py` 신설 — tmp 쓰기 + os.replace. 중단 시뮬레이션 검증:
  반쪽 파일·잔해 없음, 덮어쓰기 중단 시 기존 파일 무손상)
- [x] **정정본 PDF 미반영** — `scripts/extractor_v1.py` (2026-07-07 완료: 추출 시
  source.sha256 지문 기록, already_done 이 해시 비교 — 정정본 감지 검증 통과.
  기존 767개 산출물 지문 백필 완료)
- [x] **소스별 실패가 exit 0 으로 삼켜짐** — 5개 스테이지 (2026-07-07 완료:
  실패 목록을 data/v1/reports/failures/{stage}_failures.txt 에 기록 + exit 1 로
  run_pipeline 이 감지. 실패 0건이면 스테일 목록 자동 삭제)
- [x] **임베딩 재시도가 영구 오류(400/401)도 재시도** — `scripts/embeddings_v1.py`
  (2026-07-07 완료: 재시도는 RateLimit/Timeout/Connection/5xx 만 — 401·400 즉시 실패 검증)
- [x] **PDF 다운로드 무결성** — `scripts/crawl_pdfs.py` (2026-07-07 완료: .part 임시 파일 +
  %PDF 매직 확인 + os.replace, 기본 증분 모드(--refresh 로 전체 재다운로드), 오류 시 exit 1.
  오프라인 5케이스 검증)
- [x] **○(U+25CB) 마커 불일치** — (2026-07-07 완료: 767개 source 전수 조사 결과
  ○ 줄 시작 0회(실사용 없음) → 게이트를 파서 기준 [◯◎] 으로 통일 + 양쪽 상호 참조 주석)

## 5순위 — 설정·배포 준비

- [x] **scripts/requirements.txt 에 `pdfplumber`, `openai` 추가** (2026-07-06 완료)
- [x] **죽은 env 키 정리** — `.env.example` (2026-07-07 완료: OPENAI_*_MODEL 제거 +
  모델명이 코드 상수인 이유 주석, BACKEND_CORS_ORIGINS 는 코드가 실제로 읽음)
- [x] **CORS·API 주소 하드코딩** — (2026-07-07 완료: BACKEND_CORS_ORIGINS 환경변수화,
  api.js 는 VITE_API_URL 지원 기존재)
- [x] **검색 인덱스 생성 스크립트 부재** — `db/indexes.sql` 신설 (2026-07-07 완료:
  pg_trgm 3종 + HNSW, 실 DB 멱등 확인)
- [x] **키워드 검색 LIKE 와일드카드 미이스케이프** — `backend/search_keyword.py`
  (2026-07-07 완료: %·_·\\ 이스케이프, 50%% 오염 7,821→1,039건 실측, 테스트 신설)
- [x] **frontend 요청 타임아웃/취소 없음** — `frontend/src/api.js` (2026-07-07 완료:
  AbortSignal.timeout 일반 20초/query 90초 + 백엔드 detail 표시)
- [x] **index.html lang="en" / title "frontend"** — (2026-07-07 완료: lang="ko",
  title "국회 회의록 RAG")
- [ ] **rate limit·인증 없음** (2026-07-06 평가 보고서 기준 8 에서 추가) — 질문 1건 = LLM 비용이라
  공개 배포 시 비용 공격에 무방비. 배포(4단계) 착수 조건.

## 6순위 — 중장기 (시간 들여서)

- [ ] **답변 품질 평가 세트** — Recall@5 와 별개로 "근거 해석 정확도", "여야 분류 정확도" 평가
  (source_block 로그 축적 중 + scripts/quality_report.py 검토 큐가 재료. 수동 라벨 필요 — 미완)
- [x] **날짜 범위 질문 처리** — `backend/query_parser.py` (2026-07-07 완료: 날짜 전부 수집해
  min~max 기간, 연도 없는 뒷날짜는 앞 연도 상속. 테스트 6건, eval 무회귀)
- [x] **role=NULL 발언자 정당 오라벨** — `backend/party.py` (2026-07-07 완료: 자격 불명도
  무표기. 실측 role=NULL 0.12%·의원일치 114청크 — 라벨 손실 미미)
- [x] **22대 하드코딩 정리** — `scripts/committees.py` 신설 (2026-07-07 완료: 4파일 중복을
  단일 출처로. import 검증 + 재실행 + 테스트 통과)
- [x] **HTTP API 계층 테스트** — `tests/test_api.py` (2026-07-07 완료: TestClient 15건 —
  사전차단·502·검증 422·404, DB 없으면 skip. 총 43건)
- [x] **로그 실패 관측성** — `backend/main.py` (2026-07-07 완료: 구조화 로깅 + 요청 ID +
  실패 카운터 /health 노출)

## 7순위 — A+ 로드맵 신규 항목 (2026-07-07 추가, 상용 수준 목표 시)

> 결함이 아니라 업그레이드. ★ = 추천 경로 (효과 대비 비용 우수).
> 기준 번호는 llm_comparison_report.md 의 10가지 평가 기준.

**측정·자동화 (기준 4·9 — 추천 1·2순위)**
- [x] ★ **CI 구축** — `.github/workflows/ci.yml` (2026-07-07 완료: push 마다 pytest(커버리지)+
  lint+vitest+build. 첫 run success 확인)
- [x] 프론트 테스트 — `frontend/src/api.test.js` (2026-07-07 완료: vitest 5건, 에러 매핑)
- [x] 커버리지 측정 — pytest-cov (2026-07-07 완료: CI 에서 --cov 리포트)
  (답변 평가셋·HTTP API 테스트는 6순위에 기존재)

**검색·답변 품질 (기준 2·3·5)**
- [ ] eval 잔여 실패 2건 해소 — 윤후덕+재외국민 표현 불일치, 대일외교 시기별 (미완)
- [ ] 재순위(reranker) 도입 실험 — 하이브리드 상위 30 → 재순위 후 상위 N
- [ ] 답변-근거 일치 자동 검증 — 문장 단위 entailment 또는 LLM judge 2차 확인
- [ ] 위원회 오배치 자동 검출을 grounding 판정에 편입 (현재 수동 휴리스틱)
- [ ] 토큰 예산 eval 튜닝 — 근거 수·복원 상한(4,000자)을 답변 평가셋으로 최적화

**성능·비용 (기준 6)**
- [x] ★ 검색 두 축 병렬화 — `backend/search_hybrid.py` (2026-07-07 완료: 3.08→1.94s)
- [ ] 답변 스트리밍 — 체감 지연 절반 (프론트 SSE 수신 포함)
- [x] 질문 임베딩 캐시 — `backend/search_vector.py` (2026-07-07 완료: lru_cache, 2회차 0ms)
- [x] 일별 비용 집계 리포트 — `scripts/quality_report.py` (2026-07-07 완료)

**운영·관측성 (기준 7)**
- [x] 요청 ID 구조화 로깅 — `backend/main.py` (2026-07-07 완료)
- [x] PARTIAL+ungrounded 자동 검토 큐 — `scripts/quality_report.py` (2026-07-07 완료:
  무인용 PARTIAL·invalid citation 행을 검토 큐로 — 실데이터 3건 발굴 확인)
- [x] 주간 품질 리포트 스크립트 — `scripts/quality_report.py` (2026-07-07 완료: grounding·비용·검토 큐)

**공개 준비 (기준 8 — rate limit·CORS 는 5순위에 기존재)**
- [ ] HTTPS 배포 (4단계에서)
- [x] 프롬프트 주입 점검 — `backend/answer.py` (2026-07-07 완료: 실취약점 발견·방어 2겹, 재점검 통과)
- [ ] 월 비용 상한 알림
- [x] 의존성 취약점 스캔 — CI 편입 (2026-07-07 완료: pip-audit + npm audit 리포트,
  현재 npm 0건. 빌드는 실패시키지 않고 리포트만)

**데이터 최신성 (기준 1·2·10)**
- [ ] ★ 신규 회의록 자동 증분 인입 — 스케줄 크롤링 → 증분 파이프라인 → 증분 임베딩
  (v1.3 해시 chunk_id 선행 필요 — progress.md "v1.3 개선 예정")
- [ ] 실사용자 검증 — 대상 사용자(보좌관·기자·연구자) 3~5명 사용성 테스트·반영 기록
