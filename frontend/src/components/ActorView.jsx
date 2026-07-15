import { useEffect, useRef, useState } from 'react'
import { fetchActor, searchActors } from '../api'
import MonthlyBars from './MonthlyBars'

const STANCE_KO = { support: '찬성', oppose: '반대', concern: '우려', mixed: '혼재', no_stance: '무입장' }
const STANCE_COLOR = { support: '#2563eb', oppose: '#dc2626', concern: '#d97706', mixed: '#7c3aed', no_stance: '#6b7280' }

export default function ActorView({ actor, onIssueClick, onShown }) {
  const [input, setInput] = useState(actor || '')
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)
  const [suggestions, setSuggestions] = useState([])
  const lastLoadedRef = useRef('') // 방금 조회한 이름 — 자동완성 재팝업·이중 fetch 방지

  async function load(name) {
    const q = (name || '').trim()
    if (!q) return
    lastLoadedRef.current = q
    setSuggestions([])
    setErr(null); setProfile(null); setLoading(true)
    try {
      setProfile(await fetchActor(q))
      onShown?.(q)
    } catch (e) { setErr(e.message) } finally { setLoading(false) }
  }
  useEffect(() => {
    if (actor && actor !== lastLoadedRef.current) { setInput(actor); load(actor) }
  }, [actor])

  // 자동완성 — 250ms 디바운스, 방금 조회한 이름 그대로면 띄우지 않는다
  useEffect(() => {
    const q = input.trim()
    if (!q || q === lastLoadedRef.current) { setSuggestions([]); return undefined }
    const t = setTimeout(() => {
      searchActors(q).then(d => setSuggestions(d.matches)).catch(() => setSuggestions([]))
    }, 250)
    return () => clearTimeout(t)
  }, [input])

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <span style={{ position: 'relative', display: 'inline-block', marginRight: 8 }}>
          <input value={input} onChange={e => setInput(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') load(input); if (e.key === 'Escape') setSuggestions([]) }}
                 onBlur={() => setTimeout(() => setSuggestions([]), 150)}
                 placeholder="의원 이름 (예: 김윤)" style={{ padding: '6px 8px' }} />
          {suggestions.length > 0 && (
            <ul style={{ position: 'absolute', top: '100%', left: 0, zIndex: 10, minWidth: 220,
                         margin: '4px 0 0', padding: 4, listStyle: 'none', background: '#fff',
                         border: '1px solid #dee2e6', borderRadius: 6,
                         boxShadow: '0 4px 12px rgba(0,0,0,0.08)' }}>
              {suggestions.map(m => (
                <li key={m.name}>
                  <button type="button" onMouseDown={() => { setInput(m.name); load(m.name) }}
                          style={{ width: '100%', textAlign: 'left', padding: '6px 8px', background: 'none',
                                   border: 'none', cursor: 'pointer', fontSize: 14, fontFamily: 'inherit' }}>
                    {m.name} <span style={{ color: '#868e96', fontSize: 12 }}>{m.party}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </span>
        <button onClick={() => load(input)} disabled={loading}>{loading ? '조회 중…' : '조회'}</button>
      </div>
      {err && <p style={{ color: '#6b7280' }}>{err}</p>}
      {profile && (
        <div>
          <h3 style={{ marginBottom: 4 }}>
            {profile.display_name || profile.name}{' '}
            {profile.party && <span style={{ fontSize: 13, color: '#555', border: '1px solid #ccc', borderRadius: 4, padding: '0 6px' }}>{profile.party}</span>}
          </h3>
          {profile.party_history.length > 0 && (
            <p style={{ fontSize: 12, color: '#666', margin: '2px 0 8px' }}>
              {profile.party_history.map(h => `${h.period}: ${h.label || '—'}`).join(' / ')}
            </p>
          )}
          <p style={{ fontSize: 13 }}>
            발언 {profile.totals.turns.toLocaleString()}턴 · 회의 {profile.totals.meetings}회 · {profile.totals.first} ~ {profile.totals.last}
          </p>

          <h4>위원회 분포</h4>
          {profile.by_committee.length === 1 ? (
            // 위원회 1개면 최대값 대비 막대가 무조건 100% 폭 — 의미 없는 잉크라 텍스트로
            <p style={{ fontSize: 13, margin: '2px 0' }}>
              {profile.by_committee[0].committee} {profile.by_committee[0].turns.toLocaleString()}턴 (전체 발언)
            </p>
          ) : profile.by_committee.map(c => (
            <div key={c.committee} style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '2px 0', maxWidth: 560 }}>
              <div style={{ width: 150, fontSize: 12, flexShrink: 0 }}>{c.committee}</div>
              <div style={{ flex: 1, background: '#f3f4f6', borderRadius: 3, height: 14 }}>
                <div style={{ width: `${(c.turns / profile.totals.turns) * 100}%`, background: '#2563eb', height: 14, borderRadius: 3 }} />
              </div>
              <div style={{ width: 60, fontSize: 12, textAlign: 'right', flexShrink: 0 }}>{c.turns}턴</div>
            </div>
          ))}

          <h4>월별 발언 추이</h4>
          <MonthlyBars months={profile.by_month.map(m => ({ month: m.month, value: m.turns }))}
                       unit="턴" ariaLabel="이 의원의 월별 발언 수 막대 차트" />

          <h4>이슈별 입장</h4>
          {profile.issue_stances.length > 0 ? (
            <>
              <table style={{ maxWidth: 560, width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #dee2e6' }}>
                    <th style={{ textAlign: 'left', padding: '4px 8px 4px 0' }}>이슈</th>
                    <th style={{ padding: '4px 8px' }}>입장</th>
                    <th style={{ padding: '4px 0 4px 8px' }}>발언 수</th>
                  </tr>
                </thead>
                <tbody>
                  {profile.issue_stances.map(s => (
                    <tr key={s.issue_id} onClick={() => onIssueClick(s.issue_id)} className="clickable-row"
                        style={{ cursor: 'pointer', borderBottom: '1px solid #f1f3f5' }}
                        title="클릭하면 쟁점 분석으로 이동">
                      <td style={{ padding: '5px 8px 5px 0' }}>{s.title}</td>
                      <td style={{ textAlign: 'center', padding: '5px 8px', color: STANCE_COLOR[s.stance], fontWeight: 600 }}>{STANCE_KO[s.stance]}</td>
                      <td style={{ textAlign: 'center', padding: '5px 0 5px 8px', fontSize: 12 }}>{s.total_turns}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p style={{ fontSize: 11, color: '#888' }}>입장은 LLM 자동 판정 — 방향 참고용 · 행 클릭 시 쟁점 분석으로 이동</p>
            </>
          ) : <p style={{ fontSize: 13, color: '#666' }}>판정된 이슈 없음</p>}

          <p style={{ fontSize: 13, margin: '14px 0' }}>
            <strong>발언 유형</strong>{' '}
            {Object.entries(profile.utterance_types).map(([k, v]) => `${k === 'question' ? '질의' : '진술'} ${(v * 100).toFixed(0)}%`).join(' · ') || '—'}
            {profile.top_mentions.length > 0 && (
              <>
                <span style={{ color: '#ced4da', margin: '0 10px' }}>|</span>
                <strong>주요 언급 기관</strong>{' '}
                {profile.top_mentions.map(m => `${m.org}(${m.count})`).join(', ')}
              </>
            )}
          </p>

          <h4>최근 발언</h4>
          {profile.recent_utterances.map(u => (
            <p key={u.chunk_id} style={{ fontSize: 12, color: '#444', margin: '4px 0' }}>
              [{u.date} · {u.committee}] {u.snippet}…
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
