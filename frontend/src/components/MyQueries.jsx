import { useEffect, useState } from 'react'
import { fetchMyQueries } from '../api'

const GROUNDING_KO = { FULL: '근거 충분', PARTIAL: '부분 근거', REFUSED: '답변 보류', NONE: '근거 없음' }

// 내 질문 기록 (로그인 시 질의 탭) — 클릭하면 입력창에 채워 재실행을 유도한다.
// 저장된 답변 재표시는 범위 밖 (spec).
export default function MyQueries({ user, onPick }) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    if (!open || items !== null || !user) return
    let ignore = false  // 계정 전환·언마운트 후 도착한 늦은 응답 폐기 (스테일 덮어쓰기 방지)
    fetchMyQueries()
      .then(d => { if (!ignore) setItems(d.queries) })
      .catch(e => { if (!ignore) setErr(e.message) })
    return () => { ignore = true }
  }, [open, items, user])

  // 다른 계정으로 바뀌면 캐시 무효화
  useEffect(() => { setItems(null); setErr(null) }, [user?.username])

  if (!user) return null
  return (
    <div>
      <button type="button" className="myqueries-toggle" onClick={() => setOpen(!open)}>
        {open ? '▾' : '▸'} 내 질문 기록
      </button>
      {open && (
        <>
          {err && <p className="card-note">{err}</p>}
          {items && items.length === 0 && (
            <p className="card-note">
              아직 기록이 없습니다 — 질문하면 자동으로 저장됩니다.
            </p>
          )}
          {items && items.length > 0 && (
            <ul className="myqueries-list">
              {items.map(q => (
                <li key={q.query_id}>
                  <button type="button" className="myqueries-q" onClick={() => onPick(q.question)}
                          title="클릭하면 입력창에 채워집니다">
                    {q.question}
                  </button>
                  <span className="myqueries-meta">
                    {q.created_at.slice(0, 10)} · {GROUNDING_KO[q.grounding] || q.grounding}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
