import { useEffect, useRef, useState } from 'react'
import { fetchActor, searchActors } from '../api'
import MonthlyBars from './MonthlyBars'

const STANCE_KO = { support: '찬성', oppose: '반대', concern: '우려', mixed: '혼재', no_stance: '무입장' }
const STANCE_COLOR = { support: 'var(--stance-support)', oppose: 'var(--stance-oppose)', concern: 'var(--stance-concern)', mixed: 'var(--stance-mixed)', no_stance: 'var(--stance-none)' }

// 데이터 나열 대신 읽히는 문장으로 — 쟁점 상세의 "구도 요약 문장"과 같은 처방
function buildSummary(profile) {
  const lines = []
  const total = profile.totals.turns
  const committees = [...profile.by_committee].sort((a, b) => b.turns - a.turns)
  if (committees.length === 1) {
    lines.push(`${committees[0].committee}에서만 발언 — ${total.toLocaleString()}턴 · 회의 ${profile.totals.meetings}회`)
  } else if (committees.length > 1) {
    const top = committees[0]
    const pct = Math.round((top.turns / Math.max(total, 1)) * 100)
    lines.push(`${top.committee} 중심 활동 (발언의 ${pct}%) — 총 ${total.toLocaleString()}턴 · 회의 ${profile.totals.meetings}회`)
  }
  if (profile.by_month.length > 0) {
    const peak = profile.by_month.reduce((a, b) => (b.turns > a.turns ? b : a))
    if (peak.turns > 0) lines.push(`가장 활발했던 시기는 ${peak.month} (${peak.turns}턴)`)
  }
  if (profile.issue_stances.length > 0) {
    const tops = [...profile.issue_stances].sort((a, b) => b.total_turns - a.total_turns).slice(0, 2)
    lines.push(tops.map(s => `'${s.title}'에 ${STANCE_KO[s.stance]}`).join(' · ') + ' 입장')
  }
  if (profile.top_mentions.length > 0) {
    lines.push(`자주 언급한 기관: ${profile.top_mentions.slice(0, 3).map(m => m.org).join(', ')}`)
  }
  const q = profile.utterance_types.question
  if (q >= 0.65) lines.push('발언은 질의 중심')
  else if (q > 0 && q <= 0.35) lines.push('발언은 진술(답변·보고) 중심')
  return lines
}

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
                 placeholder="의원 이름 (예: 김윤)"
                 style={{ padding: '6px 8px', fontFamily: 'inherit', border: '1px solid var(--ink-300)', borderRadius: 'var(--radius)' }} />
          {suggestions.length > 0 && (
            <ul style={{ position: 'absolute', top: '100%', left: 0, zIndex: 10, minWidth: 220,
                         margin: '4px 0 0', padding: 4, listStyle: 'none', background: 'var(--surface)',
                         border: '1px solid var(--ink-300)', borderRadius: 'var(--radius)',
                         boxShadow: '0 4px 12px rgba(0,0,0,0.08)' }}>
              {suggestions.map(m => (
                <li key={m.name}>
                  <button type="button" onMouseDown={() => { setInput(m.name); load(m.name) }}
                          style={{ width: '100%', textAlign: 'left', padding: '6px 8px', background: 'none',
                                   border: 'none', cursor: 'pointer', fontSize: 14, fontFamily: 'inherit' }}>
                    {m.name} <span style={{ color: 'var(--ink-500)', fontSize: 12 }}>{m.party}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </span>
        <button onClick={() => load(input)} disabled={loading}>{loading ? '조회 중…' : '조회'}</button>
      </div>
      {err && <p style={{ color: 'var(--stance-none)' }}>{err}</p>}
      {profile && (
        <div>
          <h3 style={{ marginBottom: 4 }}>
            {profile.display_name || profile.name}{' '}
            {profile.party && <span style={{ fontSize: 13, color: 'var(--ink-700)', border: '1px solid var(--ink-400)', borderRadius: 'var(--radius-sm)', padding: '0 6px' }}>{profile.party}</span>}
          </h3>
          {profile.party_history.length > 0 && (
            <p style={{ fontSize: 12, color: 'var(--ink-700)', margin: '2px 0 8px' }}>
              {profile.party_history.map(h => `${h.period}: ${h.label || '—'}`).join(' / ')}
            </p>
          )}
          <ul style={{ margin: '12px 0 20px', padding: '14px 18px 14px 34px', maxWidth: 640,
                       background: 'var(--ink-100)', borderRadius: 'var(--radius)', fontSize: 15, lineHeight: 1.7 }}>
            {buildSummary(profile).map((line, i) => <li key={i}>{line}</li>)}
          </ul>

          <h4>월별 발언 추이</h4>
          <MonthlyBars months={profile.by_month.map(m => ({ month: m.month, value: m.turns }))}
                       unit="턴" ariaLabel="이 의원의 월별 발언 수 막대 차트" />

          <h4>이슈별 입장</h4>
          {profile.issue_stances.length > 0 ? (
            <>
              <table style={{ maxWidth: 560, width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--ink-300)' }}>
                    <th style={{ textAlign: 'left', padding: '4px 8px 4px 0' }}>이슈</th>
                    <th style={{ padding: '4px 8px' }}>입장</th>
                    <th style={{ padding: '4px 0 4px 8px' }}>발언 수</th>
                  </tr>
                </thead>
                <tbody>
                  {profile.issue_stances.map(s => (
                    <tr key={s.issue_id} onClick={() => onIssueClick(s.issue_id)} className="clickable-row"
                        style={{ cursor: 'pointer', borderBottom: '1px solid var(--ink-200)' }}
                        title="클릭하면 쟁점 분석으로 이동">
                      <td style={{ padding: '6px 8px 6px 0' }}>{s.title}</td>
                      <td style={{ textAlign: 'center', padding: '6px 8px', color: STANCE_COLOR[s.stance], fontWeight: 600 }}>{STANCE_KO[s.stance]}</td>
                      <td style={{ textAlign: 'center', padding: '6px 0 6px 8px', fontSize: 13 }}>{s.total_turns}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p style={{ fontSize: 12, color: 'var(--ink-500)' }}>입장은 LLM 자동 판정 — 방향 참고용 · 행 클릭 시 쟁점 분석으로 이동</p>
            </>
          ) : <p style={{ fontSize: 14, color: 'var(--ink-700)' }}>판정된 이슈 없음</p>}

          <h4>최근 발언</h4>
          <ul style={{ margin: '8px 0', padding: '0 0 0 20px', maxWidth: 720 }}>
            {profile.recent_utterances.slice(0, 3).map(u => (
              <li key={u.chunk_id} style={{ fontSize: 15, color: 'var(--ink-900)', margin: '8px 0', lineHeight: 1.6 }}>
                {u.summary || `${u.snippet}…`}
                <span style={{ color: 'var(--ink-500)', fontSize: 12, marginLeft: 8 }}>{u.date} · {u.committee}</span>
              </li>
            ))}
          </ul>
          {profile.recent_utterances.some(u => u.summary) && (
            <p style={{ fontSize: 12, color: 'var(--ink-500)' }}>요약은 LLM 자동 생성 — 원문 확인은 질의 화면에서</p>
          )}
        </div>
      )}
    </div>
  )
}
