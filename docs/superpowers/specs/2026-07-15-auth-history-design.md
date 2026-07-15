# 회원가입 + 질의 히스토리 (spec)

> 2026-07-15 브레인스토밍 확정. 목표: **포트폴리오 어필** — 인증을 직접 구현하는
> 역량 증명. 기능 범위는 최소: 비회원도 전 기능 사용 가능, 로그인 시 질의 히스토리
> 저장·조회만 추가된다. 배포(4-B 런북) 직전 삽입 — 런북 영향은 env 1줄.

## 사용자 결정 (2026-07-15)

1. 목적 = 포트폴리오 어필 (사용량 제한·접근 통제 목적 아님)
2. 기능 범위 = 질의 히스토리 (비회원 기능 제약 없음)
3. 인증 방식 = **직접 구현**: 아이디+비밀번호(bcrypt) + JWT.
   이메일을 받지 않는다 — **개인정보 부담 0** (유출 시에도 외부 신원과 연결 불가).
   Supabase Auth·OAuth 기각: 관리형은 "구현했다" 어필이 약하고, OAuth 는 방문자에게
   계정을 요구 + 환경별 콜백 설정 번거로움.

## 전제

- 인증 인프라 전무. guard.py(IP rate limit + 비용 상한)는 의존성 0 자체 구현 기조.
- query_logs 가 질의마다 question·usage 등을 기록 (main.py `_log_query`).
- CORS `allow_credentials=True` 이미 설정. 프론트(Vercel)와 백엔드(Render)는
  교차 출처 — 쿠키 대신 Bearer 헤더가 단순 (Safari ITP 등 제3자 쿠키 차단 회피).
- 코퍼스 데이터와 달리 users 는 런타임 쓰기 테이블 — 배포 DB 에 자가 생성 필요
  (utterance_summaries 와 같은 패턴).

## 데이터 모델

```sql
CREATE TABLE IF NOT EXISTS users (
  user_id       SERIAL PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE,   -- 영문·숫자·한글 2~20자
  password_hash TEXT NOT NULL,          -- bcrypt (원문은 어디에도 저장 안 함)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(user_id);
```

- 히스토리 전용 테이블을 만들지 않는다 — 기존 query_logs 재사용, 히스토리 =
  `WHERE user_id = ?`. 비로그인 질의는 지금처럼 user_id NULL.
- schema.sql 에 문서화 + 코드가 시작 시 자가 생성/마이그레이션(CREATE/ALTER IF NOT
  EXISTS) — 배포 DB 에 별도 마이그레이션 단계 없음.

## 인증 코어 — 신규 `backend/auth.py`

의존성 2개 추가: `bcrypt`, `PyJWT`. **여기만 자체 구현 기조의 예외** — 비밀번호
해시와 토큰 서명은 직접 만들면 안 되는 영역이라는 판단 자체가 스토리.

| 엔드포인트 | 동작 |
|---|---|
| `POST /auth/signup` | username 형식 검증(영문·숫자·한글 2~20자) → 중복 409 → bcrypt(cost 12) 해시 저장 → 토큰 발급(가입 즉시 로그인) |
| `POST /auth/login` | 검증 후 JWT 발급. 실패는 아이디 존재 여부와 무관하게 단일 메시지 "아이디 또는 비밀번호가 올바르지 않습니다" (계정 열거 방지) |
| `GET /auth/me` | Bearer 토큰 검증 → {username}. 무효/만료 401 |

- JWT: HS256, `JWT_SECRET` env(없으면 시작 실패가 아니라 dev 기본값 + 경고 로그 —
  로컬 편의), payload {sub: user_id, username, exp: 7일}.
- 인증은 **부가 기능** — 토큰이 무효여도 질의 자체는 익명으로 통과시킨다
  (질의를 막는 401 은 /auth/me·/me/queries 만).
- **무차별 대입 방어**: `/auth/login`·`/auth/signup` 을 guard 의 강한 한도(_LLM_PATHS
  분당 5) 그룹에 편입 — 기존 방어선 재사용, 신규 코드 없음.

## 히스토리

- `/query` 처리 시 Authorization 헤더가 있고 유효하면 `_log_query` 에 user_id 전달.
  없거나 무효면 NULL (현행과 동일).
- `GET /me/queries` — 내 최근 20건: question·mode·grounding·created_at. 답변 재표시는
  범위 밖 — 히스토리 클릭 시 질문을 입력창에 채워 재실행을 유도한다 (단순함 우선).

## 프론트엔드

- 헤더 우측: 비로그인 = "로그인 / 가입" 버튼 → 모달(로그인·가입 탭 전환).
  로그인 = "{아이디}님 · 로그아웃".
- 토큰은 localStorage. api.js 가 존재 시 Authorization 자동 첨부.
  XSS 시 토큰 탈취 가능하다는 트레이드오프 수용 — 걸린 자산이 질의 히스토리뿐인
  데모라 HttpOnly 쿠키의 교차 출처 복잡성(제3자 쿠키 차단)보다 낫다. 문서에 명시.
- 로그인 상태의 질의 탭에 "내 질문 기록" 접이식 패널 (클릭 → 입력창 채움).
- 가입 폼 안내 문구: **"포트폴리오 데모 — 다른 곳에서 쓰는 비밀번호를 입력하지
  마세요. 계정과 기록은 예고 없이 초기화될 수 있습니다."**

## 배포 영향 (4-B 런북)

- Render env 에 `JWT_SECRET` 1줄 추가 (런북 갱신).
- users·query_logs 변경은 코드 자가 생성 — 코퍼스 이전 절차 변경 없음.
- requirements 에 bcrypt·PyJWT 추가.

## 테스트 (pytest)

1. signup: 형식 위반 422 · 중복 409 · 성공 시 users 행 + bcrypt 해시(원문 아님)
2. login: 성공 토큰 / 실패 메시지 단일(없는 아이디 = 틀린 비번) / 만료·위조 토큰 401
3. 히스토리: 토큰 있는 /query → user_id 기록, 무효 토큰 → NULL 로 통과(질의 성공),
   /me/queries 본인 것만
4. guard: /auth/login 이 강한 한도 그룹에 속하는지

## 범위 밖 (명시적 기각)

- 비밀번호 재설정(이메일 없으므로 불가 — 데모에서 불필요), 아이디 변경, 회원 탈퇴 UI
- 관리자 화면, 권한 등급, 사용자별 rate limit
- 저장된 답변 재표시 (히스토리는 질문 재실행 유도까지)
