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
    <div className="stack stack-lg">
      <div className="actor-search">
        <div className="actor-search-field">
          <label className="actor-search-label" htmlFor="actor-name">의원 이름</label>
          <input id="actor-name" value={input} onChange={e => setInput(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') load(input); if (e.key === 'Escape') setSuggestions([]) }}
                 onBlur={() => setTimeout(() => setSuggestions([]), 150)}
                 placeholder="예: 김윤" className="actor-search-input" />
          {suggestions.length > 0 && (
            <ul className="actor-suggest">
              {suggestions.map(m => (
                <li key={m.name}>
                  <button type="button" onMouseDown={() => { setInput(m.name); load(m.name) }}>
                    {m.name}<span className="actor-suggest-party">{m.party}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <button type="button" className="actor-search-btn" onClick={() => load(input)} disabled={loading}>
          {loading ? '조회 중…' : '조회'}
        </button>
      </div>

      {err && <div className="error">{err}</div>}

      {profile && (
        <div className="stack stack-lg">
          <div className="actor-name-row">
            <h1 className="actor-name">{profile.display_name || profile.name}</h1>
            {profile.party && <span className="actor-party-badge">{profile.party}</span>}
            {profile.party_history.length > 0 && (
              <span className="actor-party-history">
                {profile.party_history.map(h => `${h.period}: ${h.label || '—'}`).join(' / ')}
              </span>
            )}
          </div>

          <div className="actor-summary">
            <ul>
              {buildSummary(profile).map((line, i) => <li key={i}>{line}</li>)}
            </ul>
          </div>

          <div className="actor-cols">
            <div className="panel">
              <h2 className="card-title">월별 발언 추이</h2>
              <p className="card-note chart-lede">막대는 이 의원의 월별 발언 수입니다.</p>
              <MonthlyBars months={profile.by_month.map(m => ({ month: m.month, value: m.turns }))}
                           unit="턴" ariaLabel="이 의원의 월별 발언 수 막대 차트"
                           height={140} barMax={28} gap={8} />
            </div>

            <div className="panel-flush">
              <div className="panel-flush-head">
                <h2 className="card-title">이슈별 입장</h2>
              </div>
              {profile.issue_stances.length > 0 ? (
                <>
                  <div className="table-scroll">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>이슈</th>
                          <th className="col-stance">입장</th>
                          <th className="col-num">발언 수</th>
                        </tr>
                      </thead>
                      <tbody>
                        {profile.issue_stances.map(s => (
                          <tr key={s.issue_id} onClick={() => onIssueClick(s.issue_id)}
                              className="clickable-row" title="누르면 쟁점 분석으로 이동합니다">
                            <td>{s.title}</td>
                            <td className="col-stance" style={{ color: STANCE_COLOR[s.stance] }}>
                              {STANCE_KO[s.stance]}
                            </td>
                            <td className="col-num">{s.total_turns}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="card-footnote">
                    입장은 LLM 자동 판정 — 방향 참고용 · 행을 누르면 쟁점 분석으로 이동합니다.
                  </div>
                </>
              ) : (
                <p className="card-note panel-pad">판정된 이슈 없음</p>
              )}
            </div>
          </div>

          <div className="panel">
            <h2 className="card-title">최근 발언</h2>
            <div className="utterance-list chart-lede">
              {profile.recent_utterances.slice(0, 3).map(u => (
                <div className="utterance-item" key={u.chunk_id}>
                  <span className="utterance-date">{u.date}</span>
                  <span className="utterance-text">
                    {u.summary || `${u.snippet}…`}
                    <span className="utterance-committee">{u.committee}</span>
                  </span>
                </div>
              ))}
            </div>
            {profile.recent_utterances.some(u => u.summary) && (
              <p className="card-note">
                요약은 LLM 자동 생성 — 원문 확인은 질의 화면에서 하실 수 있습니다.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
