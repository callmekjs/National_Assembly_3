import { useEffect, useState } from 'react'
import { fetchIssues, fetchTimeline, fetchStances, fetchPartyStances } from '../api'

const STANCE_KO = { support: '찬성', oppose: '반대', concern: '우려', mixed: '혼재', no_stance: '무입장' }
const STANCE_COLOR = { support: '#2563eb', oppose: '#dc2626', concern: '#d97706', mixed: '#7c3aed', no_stance: '#6b7280' }

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
      <text x={pad} y={16} fontSize="11" fill="#2563eb">— 코퍼스 발언량(좌축 정규화)</text>
      <text x={pad} y={30} fontSize="11" fill="#d97706">-- 매핑 core(우축 정규화)</text>
    </svg>
  )
}

function StanceRow({ actor }) {
  const [open, setOpen] = useState(false)
  const c = actor.counts
  return (
    <>
      <tr onClick={() => setOpen(!open)} style={{ cursor: 'pointer' }}>
        <td>{actor.speaker}</td>
        <td>{actor.party || '—'}</td>
        <td><span style={{ color: STANCE_COLOR[actor.stance], fontWeight: 600 }}>{STANCE_KO[actor.stance]}</span></td>
        <td style={{ fontSize: 12 }}>찬{c.support}·반{c.oppose}·우{c.concern}·중{c.neutral}·무{c.none}</td>
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

export default function IssueView() {
  const [issues, setIssues] = useState([])
  const [sel, setSel] = useState('medical-reform')
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

  return (
    <div>
      <label>이슈: <select value={sel} onChange={e => setSel(e.target.value)}>
        {issues.map(i => <option key={i.issue_id} value={i.issue_id}>{i.title}</option>)}
      </select></label>
      {error && <p style={{ color: '#dc2626' }}>{error}</p>}
      <h3>월별 발언 추이</h3>
      {timeline ? <TimelineChart months={timeline.months} /> : <p>불러오는 중…</p>}
      <h3>여야 구도</h3>
      {partyStances ? <PartyPanel data={partyStances} /> : <p>구도 데이터 없음(판정된 이슈만 표시)</p>}
      <h3>행위자 입장 {stances ? `(${stances.actors.length}명)` : ''}</h3>
      {stances ? (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th>발언자</th><th>정당</th><th>입장</th><th>발언 수</th><th></th></tr></thead>
          <tbody>{stances.actors.map(a => <StanceRow key={a.speaker} actor={a} />)}</tbody>
        </table>
      ) : <p>입장 데이터 없음(판정된 이슈만 표시)</p>}
    </div>
  )
}
