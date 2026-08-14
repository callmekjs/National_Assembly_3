// 백엔드 API 래퍼 — 컴포넌트는 fetch 세부를 모른다.
// 127.0.0.1 고정: localhost 는 Windows 에서 IPv6 우선 시도로 +2초 (progress.md 실측)
const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

// 백엔드가 멈추면 fetch 가 영원히 안 끝나 화면이 '생성 중...'에 잠기던 문제 —
// 요청별 타임아웃 (report 모드 실측 10~18초라 /query 는 여유 있게)
const DEFAULT_TIMEOUT_MS = 20000
const QUERY_TIMEOUT_MS = 90000
const WAKE_TIMEOUT_MS = 90000   // 무료 서버 콜드스타트 실측 33.9초 (README)

// 인증 토큰 (localStorage) — XSS 시 탈취 가능하나 걸린 자산이 질의 히스토리뿐인
// 데모라 수용. HttpOnly 쿠키는 Vercel↔Render 교차 출처(제3자 쿠키 차단)에서 더 취약.
// (typeof 가드: vitest 기본 환경은 node 라 DOM/localStorage 가 없음 — api.test.js 참고)
const TOKEN_KEY = 'auth_token'
const hasStorage = typeof localStorage !== 'undefined'
export function getToken() { return hasStorage ? localStorage.getItem(TOKEN_KEY) : null }
export function setToken(t) { if (hasStorage) localStorage.setItem(TOKEN_KEY, t) }
export function clearToken() { if (hasStorage) localStorage.removeItem(TOKEN_KEY) }

// 콜드스타트 게이트 (감사 2026-08-14).
// Render free 는 15분 유휴 후 깨어나는 데 30~50초가 걸린다. 그 사이 도착한 요청이
// 20초에 포기하면, 공유 링크(?tab=issues&issue=X)로 들어온 첫 방문자는 쟁점 화면이
// 통째로 실패한 걸 보고 — 서버가 깨어나도 아무도 다시 요청하지 않아 새로고침 전까지
// 복구되지 않았다. 타임아웃을 일괄로 늘리면 진짜 장애일 때 90초를 기다리게 되므로,
// **깨우기(/health, 90초)가 끝날 때까지만 다른 요청을 붙잡아 둔다.**
// 깨우기가 실패해도 뒤 요청을 막지 않는다 — 각자 자기 타임아웃으로 진행한다.
let wakePromise = null

function wakeOnce() {
  if (!wakePromise) wakePromise = request('/health', {}, WAKE_TIMEOUT_MS, true)
  return wakePromise
}

async function request(path, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS, isWake = false) {
  if (!isWake) await wakeOnce().catch(() => {})
  const token = getToken()
  const headers = { ...(options.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) }
  let res
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers, signal: AbortSignal.timeout(timeoutMs) })
  } catch (e) {
    if (e.name === 'TimeoutError' || e.name === 'AbortError') {
      throw new Error('응답이 너무 오래 걸립니다. 잠시 후 다시 시도해주세요.')
    }
    throw new Error('서버에 연결할 수 없습니다. 백엔드가 실행 중인지 확인하세요.')
  }
  if (res.status === 502) {
    throw new Error('답변 생성에 실패했습니다. 다시 시도해주세요.')
  }
  if (!res.ok) {
    // 백엔드 detail (예: "query_id 형식이 UUID 가 아닙니다") 을 버리지 않고 표시
    let detail = ''
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch { /* JSON 아니면 무시 */ }
    const err = new Error(detail || `요청이 실패했습니다 (HTTP ${res.status})`)
    err.status = res.status
    throw err
  }
  return res.json()
}

export function fetchIssues() {
  return request('/issues')
}

export function fetchTimeline(issueId) {
  return request(`/issues/${encodeURIComponent(issueId)}/timeline`)
}

export function fetchStances(issueId) {
  return request(`/issues/${encodeURIComponent(issueId)}/stances`)
}

export function fetchPartyStances(issueId) {
  return request(`/issues/${encodeURIComponent(issueId)}/party-stances`)
}

export function fetchActor(name) {
  return request(`/actors/${encodeURIComponent(name)}`)
}

export function searchActors(q) {
  return request(`/actors?q=${encodeURIComponent(q)}`)
}

export function pingHealth() {
  // 콜드스타트(Render free 슬립) 대비 — 최대 90초 대기.
  // 다른 요청들도 이 약속(wakePromise)을 기다리므로 깨우기는 페이지당 1회다.
  return wakeOnce()
}

export function postQuery(question, mode) {
  return request('/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, mode }),
  }, QUERY_TIMEOUT_MS)
}

export function getCitation(chunkId) {
  return request(`/citations/${encodeURIComponent(chunkId)}`)
}

export function postFeedback(queryId, rating) {
  return request('/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query_id: queryId, rating }),
  })
}

export function signup(username, password) {
  return request('/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export function login(username, password) {
  return request('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export function fetchMe() {
  return request('/auth/me')
}

export function fetchMyQueries() {
  return request('/me/queries')
}
