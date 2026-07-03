// 백엔드 API 래퍼 — 컴포넌트는 fetch 세부를 모른다.
// 127.0.0.1 고정: localhost 는 Windows 에서 IPv6 우선 시도로 +2초 (progress.md 실측)
const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

async function request(path, options) {
  let res
  try {
    res = await fetch(`${API_BASE}${path}`, options)
  } catch {
    throw new Error('서버에 연결할 수 없습니다. 백엔드가 실행 중인지 확인하세요.')
  }
  if (res.status === 502) {
    throw new Error('답변 생성에 실패했습니다. 다시 시도해주세요.')
  }
  if (!res.ok) {
    throw new Error(`요청이 실패했습니다 (HTTP ${res.status})`)
  }
  return res.json()
}

export function postQuery(question, mode) {
  return request('/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, mode }),
  })
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
