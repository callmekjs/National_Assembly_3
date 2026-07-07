# 수정 체크리스트 (2026-07-06 코드 전수 검토 결과)

전체 코드 검토(Claude) + 외부 리뷰(친구) 지적을 통합한 목록.
위에서부터 순서대로 처리하는 것을 권장.

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

- [ ] **동시 6명부터 500** — `backend/db.py:26-58`
  풀 고갈 시 즉시 예외 → 대기/재시도, 죽은 연결 반납 방지 (반납 전 상태 확인).
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
- [ ] **죽은 env 키 정리** — `.env.example` 의 OPENAI_EMBEDDING_MODEL / OPENAI_CHAT_MODEL /
  BACKEND_CORS_ORIGINS 를 코드가 실제로 읽게 하거나(권장: CORS 만) 예시에서 제거.
- [ ] **CORS·API 주소 하드코딩** — `backend/main.py:35`, `frontend/src/api.js:3`
- [ ] **검색 인덱스 생성 스크립트 부재** — HNSW·pg_trgm CREATE INDEX 를
  `db/schema.sql` 또는 별도 `db/indexes.sql` 로 저장소에 커밋.
- [ ] **키워드 검색 LIKE 와일드카드 미이스케이프** — `backend/search_keyword.py:71-95`
  토큰의 `%`, `_`, `\` 이스케이프.
- [ ] **frontend 요청 타임아웃/취소 없음** — `frontend/src/App.jsx:18-31` AbortController + 타임아웃.
- [x] **index.html lang="en" / title "frontend"** — (2026-07-07 완료: lang="ko",
  title "국회 회의록 RAG")
- [ ] **rate limit·인증 없음** (2026-07-06 평가 보고서 기준 8 에서 추가) — 질문 1건 = LLM 비용이라
  공개 배포 시 비용 공격에 무방비. 배포(4단계) 착수 조건.

## 6순위 — 중장기 (시간 들여서)

- [ ] **답변 품질 평가 세트** — Recall@5 와 별개로 "근거 해석 정확도", "여야 분류 정확도" 평가
  (근거 블록 로그가 쌓이면 그걸 재료로 만들 수 있음 → 2순위 로그 항목 선행).
- [ ] **날짜 범위 질문 처리** — `backend/query_parser.py:83-99`
  "7월 14일부터 9월 1일까지" 가 첫 날짜 하루로 축소되는 문제.
- [ ] **role=NULL 발언자 정당 오라벨** — `backend/party.py:129-139`
  증인·참고인이 동명 의원과 겹치는 경우.
- [ ] **22대 하드코딩 정리** — 위원회 코드/명칭이 crawl_pdfs.py, extractor_v1.py,
  manifest_builder.py, inspect_pdf_samples.py 4곳에 중복 → 공용 모듈 1곳으로.
- [ ] **HTTP API 계층 테스트** — FastAPI TestClient 로 /query 오케스트레이션, 502 매핑,
  /feedback 검증, /citations 404 커버.
- [ ] **로그 실패 관측성** (2026-07-06 평가 보고서 기준 7 에서 추가) — `_log_query` 실패가
  print 로만 남음. 규모 확대 시 실패 카운터/알림 연결.
