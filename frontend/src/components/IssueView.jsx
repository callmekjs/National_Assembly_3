import { useEffect, useState } from 'react'
import { fetchIssues, fetchTimeline, fetchStances, fetchPartyStances } from '../api'

const STANCE_KO = { support: '찬성', oppose: '반대', concern: '우려', mixed: '혼재', no_stance: '무입장' }
const STANCE_COLOR = { support: '#2563eb', oppose: '#dc2626', concern: '#d97706', mixed: '#7c3aed', no_stance: '#6b7280' }

// 발언 단위 판정(counts) 축 — 행위자 대표 입장(STANCE_*)과 키가 다르다 (neutral·none)
const COUNT_ORDER = ['support', 'oppose', 'concern', 'neutral', 'none']
const COUNT_KO = { support: '찬성', oppose: '반대', concern: '우려', neutral: '중립', none: '판정외' }
const COUNT_COLOR = { support: '#2563eb', oppose: '#dc2626', concern: '#d97706', neutral: '#9ca3af', none: '#e5e7eb' }

function StanceMiniBar({ counts }) {
  const total = COUNT_ORDER.reduce((s, k) => s + (counts[k] || 0), 0)
  if (!total) return <span style={{ fontSize: 12, color: '#868e96' }}>—</span>
  const tooltip = COUNT_ORDER.filter(k => counts[k] > 0)
    .map(k => `${COUNT_KO[k]} ${counts[k]}`).join(' · ')
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }} title={tooltip}>
      <div style={{ flex: 1, display: 'flex', height: 14, borderRadius: 3, overflow: 'hidden',
                    background: '#f3f4f6', minWidth: 90 }}>
        {COUNT_ORDER.filter(k => counts[k] > 0).map(k => (
          <div key={k} style={{ width: `${(counts[k] / total) * 100}%`, background: COUNT_COLOR[k] }} />
        ))}
      </div>
      <span style={{ width: 34, fontSize: 12, color: '#495057', textAlign: 'right', flexShrink: 0 }}>{total}</span>
    </div>
  )
}

function PartyBar({ row }) {
  const total = Math.max(row.actor_count, 1)
  const badge = row.side_by_period
    ? (row.side_by_period[0] === row.side_by_period[1]
        ? row.side_by_period[0] : `${row.side_by_period[0]}→${row.side_by_period[1]}`)
    : null
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '4px 0' }}>
      <div style={{ width: 170, fontSize: 13, flexShrink: 0 }}>
        {row.party}{' '}
        {badge && <span style={{ fontSize: 11, color: '#555', border: '1px solid #ccc', borderRadius: 4, padding: '0 4px' }}>{badge}</span>}
      </div>
      <div style={{ flex: 1, display: 'flex', height: 18, borderRadius: 3, overflow: 'hidden', background: '#f3f4f6' }}>
        {Object.entries(row.stance_dist).filter(([, v]) => v > 0).map(([s, v]) => (
          <div key={s} title={`${STANCE_KO[s]} ${v}명`}
               style={{ width: `${(v / total) * 100}%`, background: STANCE_COLOR[s] }} />
        ))}
      </div>
      <div style={{ width: 44, fontSize: 12, textAlign: 'right', flexShrink: 0 }}>{row.actor_count}명</div>
    </div>
  )
}

function PartyPanel({ data }) {
  if (!data) return <p>불러오는 중…</p>
  return (
    <div>
      {data.mapping_quality === 'low' && (
        <p style={{ color: '#d97706', fontSize: 12 }}>
          ⚠ 이 이슈의 청크 매핑 정밀도는 게이트 기준(90%) 미달 — 구도 수치 해석 주의
        </p>
      )}
      {data.parties.map(r => <PartyBar key={r.party} row={r} />)}
      <p style={{ fontSize: 11, color: '#666' }}>
        {Object.entries(STANCE_KO).map(([s, ko]) => (
          <span key={s} style={{ marginRight: 10 }}>
            <span style={{ color: STANCE_COLOR[s] }}>■</span> {ko}
          </span>
        ))}
      </p>
    </div>
  )
}

function TimelineChart({ months }) {
  if (!months || months.length === 0) return <p>타임라인 데이터 없음</p>
  const W = 640, H = 200, pad = 30
  const maxC = Math.max(...months.map(m => m.corpus_turns), 1)
  const maxM = Math.max(...months.map(m => m.mapped_core_turns), 1)
  const x = i => pad + i * (W - 2 * pad) / Math.max(months.length - 1, 1)
  const yC = v => H - pad - v / maxC * (H - 2 * pad)
  const yM = v => H - pad - v / maxM * (H - 2 * pad)
  const line = (fy, key) => months.map((m, i) => `${x(i).toFixed(1)},${fy(m[key]).toFixed(1)}`).join(' ')
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="이슈 월별 발언 추이">
      <polyline fill="none" stroke="#2563eb" strokeWidth="2" points={line(yC, 'corpus_turns')} />
      <polyline fill="none" stroke="#d97706" strokeWidth="1.5" strokeDasharray="5 3" points={line(yM, 'mapped_core_turns')} />
      <text x={pad} y={H - 8} fontSize="11" fill="#666">{months[0].month}</text>
      <text x={W - pad} y={H - 8} fontSize="11" fill="#666" textAnchor="end">{months[months.length - 1].month}</text>
      <text x={pad} y={16} fontSize="11" fill="#2563eb">— 전체 발언 추이</text>
      <text x={pad} y={30} fontSize="11" fill="#d97706">-- 이 쟁점 발언 추이 (상대 비율)</text>
    </svg>
  )
}

function StanceRow({ actor, onActorClick }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <tr onClick={() => setOpen(!open)} style={{ cursor: 'pointer' }}>
        <td onClick={e => { e.stopPropagation(); onActorClick?.(actor.speaker) }}
            style={{ color: '#2563eb', textDecoration: 'underline', cursor: 'pointer' }}
            title="의원 프로필 보기">{actor.speaker}</td>
        <td>{actor.party || '—'}</td>
        <td><span style={{ color: STANCE_COLOR[actor.stance], fontWeight: 600 }}>{STANCE_KO[actor.stance]}</span></td>
        <td style={{ padding: '2px 8px' }}><StanceMiniBar counts={actor.counts} /></td>
        <td>{open ? '▲' : '▼'}</td>
      </tr>
      {open && actor.citations.map(cit => (
        <tr key={cit.turn_id}><td colSpan="5" style={{ fontSize: 12, color: '#444', padding: '4px 12px' }}>
          [{STANCE_KO[cit.stance] || cit.stance} · {cit.date}] {cit.snippet}…
        </td></tr>
      ))}
    </>
  )
}

function IssueGrid({ issues, onPick }) {
  if (!issues.length) return <p>불러오는 중…</p>
  return (
    <div className="issue-grid">
      {issues.map(i => (
        <button key={i.issue_id} type="button" className="issue-card" onClick={() => onPick(i.issue_id)}>
          <div className="issue-card-title">{i.title}</div>
          <div className="issue-card-desc">{i.description}</div>
          <div className="issue-card-meta">발언 {(i.turn_count ?? 0).toLocaleString()}건</div>
        </button>
      ))}
    </div>
  )
}

export default function IssueView({ selectedIssue, onActorClick, onSelChange }) {
  const [issues, setIssues] = useState([])
  const [sel, setSel] = useState(selectedIssue || null) // null = 쟁점 카드 목록
  useEffect(() => { if (selectedIssue) setSel(selectedIssue) }, [selectedIssue])
  const [timeline, setTimeline] = useState(null)
  const [stances, setStances] = useState(null)
  const [partyStances, setPartyStances] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => { fetchIssues().then(d => setIssues(d.issues)).catch(e => setError(e.message)) }, [])
  useEffect(() => {
    if (!sel) return
    setError(null); setTimeline(null); setStances(null); setPartyStances(null)
    fetchTimeline(sel).then(setTimeline).catch(e => setError(e.message))
    fetchStances(sel).then(setStances).catch(e => setError(e.message))
    fetchPartyStances(sel).then(setPartyStances).catch(() => setPartyStances(null))
  }, [sel])

  function pick(id) { setSel(id); onSelChange?.(id) }

  if (!sel) {
    return (
      <div>
        {error && <p style={{ color: '#dc2626' }}>{error}</p>}
        <p style={{ fontSize: 13, color: '#495057', marginBottom: 12 }}>
          국회가 다룬 24개 쟁점 — 카드를 누르면 발언 추이·여야 구도·행위자 입장을 봅니다.
        </p>
        <IssueGrid issues={issues} onPick={pick} />
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <button type="button" onClick={() => pick(null)}
                style={{ padding: '4px 10px', fontSize: 13, fontFamily: 'inherit', background: '#fff',
                         color: '#495057', border: '1px solid #dee2e6', borderRadius: 6, cursor: 'pointer' }}>
          ← 전체 쟁점
        </button>
        <label>이슈: <select value={sel} onChange={e => pick(e.target.value)}>
          {issues.map(i => <option key={i.issue_id} value={i.issue_id}>{i.title}</option>)}
        </select></label>
      </div>
      {error && <p style={{ color: '#dc2626' }}>{error}</p>}
      <h3>월별 발언 추이</h3>
      {timeline ? <TimelineChart months={timeline.months} /> : <p>불러오는 중…</p>}
      <h3>여야 구도</h3>
      {partyStances ? <PartyPanel data={partyStances} /> : <p>구도 데이터 없음(판정된 이슈만 표시)</p>}
      <h3>행위자 입장 {stances ? `(${stances.actors.length}명)` : ''}</h3>
      {stances ? (
        <>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th>발언자</th><th>정당</th><th>입장</th><th style={{ minWidth: 140 }}>입장 분포</th><th></th></tr></thead>
            <tbody>{stances.actors.map(a => <StanceRow key={a.speaker} actor={a} onActorClick={onActorClick} />)}</tbody>
          </table>
          <p style={{ fontSize: 11, color: '#666' }}>
            {COUNT_ORDER.map(s => (
              <span key={s} style={{ marginRight: 10 }}>
                <span style={{ color: COUNT_COLOR[s] }}>■</span> {COUNT_KO[s]}
              </span>
            ))}
            <span style={{ color: '#868e96' }}>— 막대는 발언 단위 판정 비율, 숫자는 판정 발언 수</span>
          </p>
        </>
      ) : <p>입장 데이터 없음(판정된 이슈만 표시)</p>}
    </div>
  )
}
