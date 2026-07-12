import { useEffect, useState } from 'react'
import { fetchActor } from '../api'

const STANCE_KO = { support: '찬성', oppose: '반대', concern: '우려', mixed: '혼재', no_stance: '무입장' }
const STANCE_COLOR = { support: '#2563eb', oppose: '#dc2626', concern: '#d97706', mixed: '#7c3aed', no_stance: '#6b7280' }

function MonthLine({ months }) {
  if (!months || months.length < 2) return null
  const W = 640, H = 110, pad = 24
  const max = Math.max(...months.map(m => m.turns), 1)
  const x = i => pad + i * (W - 2 * pad) / (months.length - 1)
  const y = v => H - pad + 6 - v / max * (H - 2 * pad)
  const pts = months.map((m, i) => `${x(i).toFixed(1)},${y(m.turns).toFixed(1)}`).join(' ')
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="월별 발언 추이">
      <polyline fill="none" stroke="#2563eb" strokeWidth="2" points={pts} />
      <text x={pad} y={H - 4} fontSize="11" fill="#666">{months[0].month}</text>
      <text x={W - pad} y={H - 4} fontSize="11" fill="#666" textAnchor="end">{months[months.length - 1].month}</text>
    </svg>
  )
}

export default function ActorView({ actor, onIssueClick }) {
  const [input, setInput] = useState(actor || '')
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)

  async function load(name) {
    const q = (name || '').trim()
    if (!q) return
    setErr(null); setProfile(null); setLoading(true)
    try { setProfile(await fetchActor(q)) } catch (e) { setErr(e.message) } finally { setLoading(false) }
  }
  useEffect(() => { if (actor) { setInput(actor); load(actor) } }, [actor])

  const maxCommittee = profile ? Math.max(...profile.by_committee.map(c => c.turns), 1) : 1

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <input value={input} onChange={e => setInput(e.target.value)}
               onKeyDown={e => e.key === 'Enter' && load(input)}
               placeholder="의원 이름 (예: 김윤)" style={{ padding: '6px 8px', marginRight: 8 }} />
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
          {profile.by_committee.map(c => (
            <div key={c.committee} style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '2px 0' }}>
              <div style={{ width: 150, fontSize: 12, flexShrink: 0 }}>{c.committee}</div>
              <div style={{ flex: 1, background: '#f3f4f6', borderRadius: 3, height: 14 }}>
                <div style={{ width: `${(c.turns / maxCommittee) * 100}%`, background: '#2563eb', height: 14, borderRadius: 3 }} />
              </div>
              <div style={{ width: 60, fontSize: 12, textAlign: 'right' }}>{c.turns}턴</div>
            </div>
          ))}

          <h4>월별 발언 추이</h4>
          <MonthLine months={profile.by_month} />

          <h4>이슈별 입장</h4>
          {profile.issue_stances.length > 0 ? (
            <>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={{ textAlign: 'left' }}>이슈</th><th>입장</th><th>발언 수</th></tr></thead>
                <tbody>
                  {profile.issue_stances.map(s => (
                    <tr key={s.issue_id} onClick={() => onIssueClick(s.issue_id)} style={{ cursor: 'pointer' }}
                        title="클릭하면 쟁점 분석으로 이동">
                      <td>{s.title}</td>
                      <td style={{ textAlign: 'center', color: STANCE_COLOR[s.stance], fontWeight: 600 }}>{STANCE_KO[s.stance]}</td>
                      <td style={{ textAlign: 'center', fontSize: 12 }}>{s.total_turns}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p style={{ fontSize: 11, color: '#888' }}>입장은 LLM 자동 판정 — 방향 참고용</p>
            </>
          ) : <p style={{ fontSize: 13, color: '#666' }}>판정된 이슈 없음</p>}

          <h4>발언 유형</h4>
          <p style={{ fontSize: 13 }}>
            {Object.entries(profile.utterance_types).map(([k, v]) => `${k === 'question' ? '질의' : '진술'} ${(v * 100).toFixed(0)}%`).join(' · ') || '—'}
          </p>

          <h4>주요 언급 기관</h4>
          <p style={{ fontSize: 13 }}>
            {profile.top_mentions.map(m => `${m.org}(${m.count})`).join(', ') || '—'}
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
