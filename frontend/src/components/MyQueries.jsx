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
    if (!open || items !== null) return
    fetchMyQueries().then(d => setItems(d.queries)).catch(e => setErr(e.message))
  }, [open, items])

  // 다른 계정으로 바뀌면 캐시 무효화
  useEffect(() => { setItems(null); setErr(null) }, [user?.username])

  if (!user) return null
  return (
    <div style={{ margin: '10px 0' }}>
      <button type="button" onClick={() => setOpen(!open)}
              style={{ fontSize: 13, padding: '4px 10px', cursor: 'pointer' }}>
        {open ? '▾' : '▸'} 내 질문 기록
      </button>
      {open && (
        <div style={{ marginTop: 6 }}>
          {err && <p style={{ fontSize: 13, color: '#c92a2a' }}>{err}</p>}
          {items && items.length === 0 && (
            <p style={{ fontSize: 13, color: '#868e96' }}>아직 기록이 없습니다 — 질문하면 자동으로 저장됩니다.</p>
          )}
          {items && items.length > 0 && (
            <ul style={{ margin: 0, padding: '0 0 0 18px', maxWidth: 720 }}>
              {items.map(q => (
                <li key={q.query_id} style={{ fontSize: 14, margin: '5px 0' }}>
                  <button type="button" onClick={() => onPick(q.question)}
                          title="클릭하면 입력창에 채워집니다"
                          style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                                   fontSize: 14, fontFamily: 'inherit', color: '#1c7ed6', textAlign: 'left' }}>
                    {q.question}
                  </button>
                  <span style={{ color: '#868e96', fontSize: 12, marginLeft: 8 }}>
                    {q.created_at.slice(0, 10)} · {GROUNDING_KO[q.grounding] || q.grounding}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
