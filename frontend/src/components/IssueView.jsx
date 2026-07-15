import { useEffect, useState } from 'react'
import { fetchIssues, fetchTimeline, fetchStances, fetchPartyStances } from '../api'
import MonthlyBars from './MonthlyBars'

const STANCE_KO = { support: '찬성', oppose: '반대', concern: '우려', mixed: '혼재', no_stance: '무입장' }
const STANCE_COLOR = { support: '#2563eb', oppose: '#dc2626', concern: '#d97706', mixed: '#7c3aed', no_stance: '#6b7280' }

// 발언 단위 판정(counts) 축 — 행위자 대표 입장(STANCE_*)과 키가 다르다 (neutral·none)
const COUNT_ORDER = ['support', 'oppose', 'concern', 'neutral', 'none']
const COUNT_KO = { support: '찬성', oppose: '반대', concern: '우려', neutral: '중립', none: '판정외' }
const COUNT_COLOR = { support: '#2563eb', oppose: '#dc2626', concern: '#d97706', neutral: '#9ca3af', none: '#e5e7eb' }

function StanceMiniBar({ counts, maxTotal }) {
  const total = COUNT_ORDER.reduce((s, k) => s + (counts[k] || 0), 0)
  if (!total) return <span style={{ fontSize: 12, color: '#868e96' }}>—</span>
  const tooltip = COUNT_ORDER.filter(k => counts[k] > 0)
    .map(k => `${COUNT_KO[k]} ${counts[k]}`).join(' · ')
  // 폭 = 발언 수 비례 (표 내 최대 기준, 하한 10%) — 4건과 35건이 같은 폭으로
  // 그려지던 시각 왜곡 제거. 정확한 수치는 우측 숫자·툴팁
  const widthPct = Math.max((total / Math.max(maxTotal, 1)) * 100, 10)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }} title={tooltip}>
      <div style={{ flex: 1, minWidth: 90 }}>
        <div style={{ width: `${widthPct}%`, display: 'flex', height: 14, borderRadius: 3, overflow: 'hidden' }}>
          {COUNT_ORDER.filter(k => counts[k] > 0).map(k => (
            <div key={k} style={{ width: `${(counts[k] / total) * 100}%`, background: COUNT_COLOR[k] }} />
          ))}
        </div>
      </div>
      <span style={{ width: 34, fontSize: 12, color: '#495057', textAlign: 'right', flexShrink: 0 }}>{total}</span>
    </div>
  )
}

function PartyBar({ row, maxCount }) {
  const total = Math.max(row.actor_count, 1)
  // 막대 폭 = 인원수 비례 — 1명 정당이 18명 정당과 같은 폭으로 그려져
  // "강한 입장 블록"으로 오독되던 왜곡 제거 (최소 4%는 클릭·툴팁용 시각 하한)
  const widthPct = Math.max((row.actor_count / Math.max(maxCount, 1)) * 100, 4)
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
      <div style={{ flex: 1 }}>
        <div style={{ width: `${widthPct}%`, display: 'flex', height: 18, borderRadius: 3, overflow: 'hidden' }}>
          {Object.entries(row.stance_dist).filter(([, v]) => v > 0).map(([s, v]) => (
            <div key={s} title={`${STANCE_KO[s]} ${v}명`}
                 style={{ width: `${(v / total) * 100}%`, background: STANCE_COLOR[s] }} />
          ))}
        </div>
      </div>
      <div style={{ width: 44, fontSize: 12, textAlign: 'right', flexShrink: 0 }}>{row.actor_count}명</div>
    </div>
  )
}

const _SUMMARY_ORDER = ['support', 'oppose', 'concern', 'mixed', 'no_stance']

function partySummary(parties) {
  // 인원 3명 이상 상위 3개 그룹의 최다 입장을 한 문장으로 — "그래서 구도가 어떤가"
  const top = [...parties].sort((a, b) => b.actor_count - a.actor_count)
    .filter(p => p.actor_count >= 3).slice(0, 3)
  if (!top.length) return null
  return top.map(p => {
    const dom = _SUMMARY_ORDER.reduce(
      (best, s) => (p.stance_dist[s] || 0) > (p.stance_dist[best] || 0) ? s : best, _SUMMARY_ORDER[0])
    return `${p.party} ${p.actor_count}명은 ${STANCE_KO[dom]} 중심`
  }).join(' · ')
}

function PartyPanel({ data }) {
  if (!data) return <p>불러오는 중…</p>
  const maxCount = Math.max(...data.parties.map(p => p.actor_count), 1)
  const summary = partySummary(data.parties)
  return (
    <div>
      {data.mapping_quality === 'low' && (
        <p style={{ color: '#d97706', fontSize: 12 }}>
          ⚠ 이 이슈의 청크 매핑 정밀도는 게이트 기준(90%) 미달 — 구도 수치 해석 주의
        </p>
      )}
      {summary && (
        <p style={{ fontSize: 13.5, fontWeight: 500, color: '#212529', margin: '2px 0 8px' }}>{summary}</p>
      )}
      {data.parties.map(r => <PartyBar key={r.party} row={r} maxCount={maxCount} />)}
      <p style={{ fontSize: 11, color: '#666' }}>
        {Object.entries(STANCE_KO).map(([s, ko]) => (
          <span key={s} style={{ marginRight: 10 }}>
            <span style={{ color: STANCE_COLOR[s] }}>■</span> {ko}
          </span>
        ))}
        <span style={{ color: '#868e96' }}>— 막대 길이는 인원수 비례</span>
      </p>
    </div>
  )
}

function TimelineChart({ months }) {
  // 이 쟁점 발언 수(절대값)의 월별 막대 하나만 — 전체 코퍼스 선(회의 일정 계절성)은
  // 배경 소음이라 제거. 독자의 질문은 "언제 뜨거웠나" 하나다. 차트 본체는 MonthlyBars 공용.
  if (!months || months.length === 0) return <p>타임라인 데이터 없음</p>
  return (
    <MonthlyBars months={months.map(m => ({ month: m.month, value: m.mapped_core_turns || 0 }))}
                 unit="건" ariaLabel="이 쟁점의 월별 발언 수 막대 차트" />
  )
}

function StanceRow({ actor, onActorClick, maxTotal }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <tr onClick={() => setOpen(!open)} style={{ cursor: 'pointer' }}>
        <td onClick={e => { e.stopPropagation(); onActorClick?.(actor.speaker) }}
            style={{ color: '#2563eb', textDecoration: 'underline', cursor: 'pointer' }}
            title="의원 프로필 보기">{actor.speaker}</td>
        <td>{actor.party || '—'}</td>
        <td><span style={{ color: STANCE_COLOR[actor.stance], fontWeight: 600 }}>{STANCE_KO[actor.stance]}</span></td>
        <td style={{ padding: '2px 8px' }}><StanceMiniBar counts={actor.counts} maxTotal={maxTotal} /></td>
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
            <tbody>
              {(() => {
                const maxTotal = Math.max(...stances.actors.map(
                  a => COUNT_ORDER.reduce((s, k) => s + (a.counts[k] || 0), 0)), 1)
                return stances.actors.map(a => (
                  <StanceRow key={a.speaker} actor={a} onActorClick={onActorClick} maxTotal={maxTotal} />
                ))
              })()}
            </tbody>
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
