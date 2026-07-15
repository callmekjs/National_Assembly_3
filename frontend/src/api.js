// 백엔드 API 래퍼 — 컴포넌트는 fetch 세부를 모른다.
// 127.0.0.1 고정: localhost 는 Windows 에서 IPv6 우선 시도로 +2초 (progress.md 실측)
const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

// 백엔드가 멈추면 fetch 가 영원히 안 끝나 화면이 '생성 중...'에 잠기던 문제 —
// 요청별 타임아웃 (report 모드 실측 10~18초라 /query 는 여유 있게)
const DEFAULT_TIMEOUT_MS = 20000
const QUERY_TIMEOUT_MS = 90000

// 인증 토큰 (localStorage) — XSS 시 탈취 가능하나 걸린 자산이 질의 히스토리뿐인
// 데모라 수용. HttpOnly 쿠키는 Vercel↔Render 교차 출처(제3자 쿠키 차단)에서 더 취약.
// (typeof 가드: vitest 기본 환경은 node 라 DOM/localStorage 가 없음 — api.test.js 참고)
const TOKEN_KEY = 'auth_token'
const hasStorage = typeof localStorage !== 'undefined'
export function getToken() { return hasStorage ? localStorage.getItem(TOKEN_KEY) : null }
export function setToken(t) { if (hasStorage) localStorage.setItem(TOKEN_KEY, t) }
export function clearToken() { if (hasStorage) localStorage.removeItem(TOKEN_KEY) }

async function request(path, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
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
    throw new Error(detail || `요청이 실패했습니다 (HTTP ${res.status})`)
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
  // 콜드스타트(Render free 슬립) 대비 — 최대 90초 대기
  return request('/health', {}, 90000)
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
