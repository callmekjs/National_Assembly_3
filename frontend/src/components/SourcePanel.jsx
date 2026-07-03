// 근거 카드 목록 — sources 전체를 보여주고 실제 인용된 카드만 "인용됨" 표시.
// 전달됐지만 인용 안 된 근거도 투명하게 노출한다 (신뢰 설계).
function SourcePanel({ sources, citedNumbers, highlightN, onOpenSource }) {
  if (!sources.length) {
    return (
      <div className="source-panel">
        <h2>출처</h2>
        <p className="no-sources">관련 근거를 찾지 못했습니다.</p>
      </div>
    )
  }

  return (
    <div className="source-panel">
      <h2>출처 ({sources.length})</h2>
      <ul className="source-list">
        {sources.map((s) => (
          <li
            key={s.n}
            id={`source-${s.n}`}
            className={`source-card${highlightN === s.n ? ' highlighted' : ''}`}
          >
            <button type="button" onClick={() => onOpenSource(s.chunk_id)}>
              <div className="source-head">
                <span className="source-n">[{s.n}]</span>
                <span className="source-speaker">
                  {s.speaker}
                  {s.role ? ` ${s.role}` : ''}
                </span>
                {citedNumbers.includes(s.n) && <span className="cited-badge">인용됨</span>}
              </div>
              <div className="source-meta">
                {s.party ? `${s.party} · ` : ''}
                {s.committee} · {s.date} · p.{s.page_start}
              </div>
              <div className="source-snippet">{s.snippet}</div>
              <div className="source-open">원문 보기 →</div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default SourcePanel
