import { useEffect, useState } from 'react'
import { fetchIssues, fetchTimeline, fetchStances, fetchPartyStances } from '../api'
import MonthlyBars from './MonthlyBars'

const STANCE_KO = { support: '찬성', oppose: '반대', concern: '우려', mixed: '혼재', no_stance: '무입장' }
const STANCE_COLOR = { support: 'var(--stance-support)', oppose: 'var(--stance-oppose)', concern: 'var(--stance-concern)', mixed: 'var(--stance-mixed)', no_stance: 'var(--stance-none)' }

// 발언 단위 판정(counts) 축 — 행위자 대표 입장(STANCE_*)과 키가 다르다 (neutral·none)
const COUNT_ORDER = ['support', 'oppose', 'concern', 'neutral', 'none']
const COUNT_KO = { support: '찬성', oppose: '반대', concern: '우려', neutral: '중립', none: '판정외' }
const COUNT_COLOR = { support: 'var(--stance-support)', oppose: 'var(--stance-oppose)', concern: 'var(--stance-concern)', neutral: 'var(--stance-neutral)', none: 'var(--ink-200)' }

const countTotal = (counts) => COUNT_ORDER.reduce((s, k) => s + (counts[k] || 0), 0)

function StanceMiniBar({ counts, maxTotal }) {
  const total = countTotal(counts)
  if (!total) return <span className="stance-dash">—</span>
  const tooltip = COUNT_ORDER.filter(k => counts[k] > 0)
    .map(k => `${COUNT_KO[k]} ${counts[k]}`).join(' · ')
  // 폭 = 발언 수 비례 (표 내 최대 기준, 하한 10%) — 4건과 35건이 같은 폭으로
  // 그려지던 시각 왜곡 제거. 정확한 수치는 우측 '발언 수' 열·툴팁
  const widthPct = Math.max((total / Math.max(maxTotal, 1)) * 100, 10)
  return (
    <span className="mini-bar" style={{ width: `${widthPct}%` }} title={tooltip}>
      {COUNT_ORDER.filter(k => counts[k] > 0).map(k => (
        <span key={k} style={{ width: `${(counts[k] / total) * 100}%`, background: COUNT_COLOR[k] }} />
      ))}
    </span>
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
    <div className="party-row">
      <span className="party-name">
        {row.party}
        {badge && <span className="party-badge">{badge}</span>}
      </span>
      <span className="party-track">
        <span className="party-fill" style={{ width: `${widthPct}%` }}>
          {Object.entries(row.stance_dist).filter(([, v]) => v > 0).map(([s, v]) => (
            <span key={s} title={`${STANCE_KO[s]} ${v}명`}
                  style={{ width: `${(v / total) * 100}%`, background: STANCE_COLOR[s] }} />
          ))}
        </span>
      </span>
      <span className="party-count"><span className="num">{row.actor_count}</span>명</span>
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

function Legend({ order, labels, colors, note, inline = false }) {
  return (
    <div className={`legend${inline ? ' is-inline' : ''}`}>
      {order.map(s => (
        <span className="legend-item" key={s}>
          <span className="legend-swatch" style={{ background: colors[s] }} />
          {labels[s]}
        </span>
      ))}
      {note && <span className="legend-note">{note}</span>}
    </div>
  )
}

function PartyPanel({ data }) {
  if (!data) return <p className="card-note">불러오는 중…</p>
  const maxCount = Math.max(...data.parties.map(p => p.actor_count), 1)
  const summary = partySummary(data.parties)
  return (
    <>
      {summary && <p className="party-summary">{summary}</p>}
      <div className="party-rows">
        {data.parties.map(r => <PartyBar key={r.party} row={r} maxCount={maxCount} />)}
      </div>
      <Legend order={_SUMMARY_ORDER} labels={STANCE_KO} colors={STANCE_COLOR}
              note="— 막대 길이는 인원수 비례" />
    </>
  )
}

function StanceRow({ actor, onActorClick, maxTotal }) {
  const [open, setOpen] = useState(false)
  const total = countTotal(actor.counts)
  return (
    <>
      <tr onClick={() => setOpen(!open)} className={`clickable-row${open ? ' stance-row-open' : ''}`}
          aria-expanded={open} title="누르면 인용 발언이 펼쳐집니다">
        <td>
          <button type="button" className="link-btn" title="의원 프로필 보기"
                  onClick={e => { e.stopPropagation(); onActorClick?.(actor.speaker) }}>
            {actor.speaker}
          </button>
        </td>
        <td className="col-party">{actor.party || '—'}</td>
        <td className="col-stance" style={{ color: STANCE_COLOR[actor.stance] }}>
          {STANCE_KO[actor.stance]}
        </td>
        <td className="col-dist"><StanceMiniBar counts={actor.counts} maxTotal={maxTotal} /></td>
        <td className="col-num">{total}</td>
      </tr>
      {open && actor.citations.map(cit => (
        <tr key={cit.turn_id} className="stance-row-open">
          <td colSpan="5" className="stance-citation">
            [{STANCE_KO[cit.stance] || cit.stance} · {cit.date}] {cit.snippet}…
          </td>
        </tr>
      ))}
    </>
  )
}

function IssueGrid({ issues, onPick }) {
  if (!issues.length) return <p className="card-note">불러오는 중…</p>
  return (
    <div className="issue-grid">
      {issues.map(i => (
        <button key={i.issue_id} type="button" className="issue-card" onClick={() => onPick(i.issue_id)}>
          <span className="issue-card-title">{i.title}</span>
          <span className="issue-card-desc">{i.description}</span>
          <span className="issue-card-meta">발언 {(i.turn_count ?? 0).toLocaleString()}건</span>
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
      <div className="stack stack-md">
        {error && <div className="error">{error}</div>}
        <div>
          <h1 className="view-title">쟁점 분석</h1>
          <p className="view-desc">
            국회가 다룬 24개 쟁점 — 카드를 누르면 발언 추이·여야 구도·행위자 입장을 봅니다.
          </p>
        </div>
        <IssueGrid issues={issues} onPick={pick} />
      </div>
    )
  }

  const current = issues.find(i => i.issue_id === sel)
  const months = timeline?.months ?? []
  const range = months.length ? `${months[0].month} ~ ${months[months.length - 1].month}` : null
  const maxTotal = stances
    ? Math.max(...stances.actors.map(a => countTotal(a.counts)), 1)
    : 1

  return (
    <div className="stack stack-lg">
      <div className="detail-head">
        <button type="button" className="btn-back" onClick={() => pick(null)}>← 전체 쟁점</button>
        <h1 className="view-title">{current?.title ?? sel}</h1>
        <span className="detail-meta">
          {current ? `발언 ${(current.turn_count ?? 0).toLocaleString()}건` : ''}
          {current && range ? ' · ' : ''}
          {range || ''}
        </span>
        <select className="issue-select" value={sel} onChange={e => pick(e.target.value)}
                aria-label="쟁점 전환">
          {issues.map(i => <option key={i.issue_id} value={i.issue_id}>{i.title}</option>)}
        </select>
      </div>

      {error && <div className="error">{error}</div>}

      {partyStances?.mapping_quality === 'low' && (
        <p className="warn-line">
          ⚠ 이 이슈의 청크 매핑 정밀도는 게이트 기준(90%) 미달 — 구도 수치 해석 주의
        </p>
      )}

      <div className="panel">
        <h2 className="card-title">월별 발언 추이</h2>
        <p className="card-note chart-lede">막대는 이 쟁점으로 판정된 발언 수입니다.</p>
        {timeline ? (
          <MonthlyBars
            months={months.map(m => ({ month: m.month, value: m.mapped_core_turns || 0 }))}
            unit="건" ariaLabel="이 쟁점의 월별 발언 수 막대 차트"
          />
        ) : <p className="card-note">불러오는 중…</p>}
      </div>

      <div className="panel">
        <h2 className="card-title">여야 구도</h2>
        {partyStances
          ? <PartyPanel data={partyStances} />
          : <p className="card-note">구도 데이터 없음(판정된 이슈만 표시)</p>}
      </div>

      <div className="panel-flush">
        <div className="panel-flush-head">
          <h2 className="card-title">
            행위자 입장{stances ? ` (${stances.actors.length}명)` : ''}
          </h2>
          <p className="card-note">
            입장은 LLM 자동 판정 — 방향 참고용 · 이름을 누르면 의원 프로필로 이동합니다.
          </p>
        </div>

        {stances ? (
          <>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>발언자</th>
                    <th>정당</th>
                    <th>대표 입장</th>
                    <th className="col-dist">발언 단위 판정 분포</th>
                    <th className="col-num">발언 수</th>
                  </tr>
                </thead>
                <tbody>
                  {stances.actors.map(a => (
                    <StanceRow key={a.speaker} actor={a} onActorClick={onActorClick} maxTotal={maxTotal} />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="card-footnote">
              <Legend order={COUNT_ORDER} labels={COUNT_KO} colors={COUNT_COLOR} inline
                      note="— 막대는 발언 단위 판정 비율, 막대 길이는 표 내 최대 발언 수 기준입니다." />
            </div>
          </>
        ) : (
          <p className="card-note panel-pad">입장 데이터 없음(판정된 이슈만 표시)</p>
        )}
      </div>
    </div>
  )
}
