// 근거 카드 목록 — sources 전체를 보여주고 실제 인용된 카드만 "인용됨" 표시.
// 전달됐지만 인용 안 된 근거도 투명하게 노출한다 (신뢰 설계).
function SourcePanel({ sources, citedNumbers, highlightN, onOpenSource }) {
  if (!sources.length) {
    return (
      <div className="source-panel">
        <div className="source-panel-head">
          <h2>출처</h2>
        </div>
        <p className="no-sources">관련 근거를 찾지 못했습니다.</p>
      </div>
    )
  }

  return (
    <div className="source-panel">
      <div className="source-panel-head">
        <h2>출처</h2>
        <span className="source-panel-count">
          <span className="num">{sources.length}</span>건 중{' '}
          <span className="num">{citedNumbers.length}</span>건 인용
        </span>
      </div>

      <ul className="source-list">
        {sources.map((s) => {
          const cited = citedNumbers.includes(s.n)
          return (
            <li
              key={s.n}
              id={`source-${s.n}`}
              className={`source-card${highlightN === s.n ? ' highlighted' : ''}`}
            >
              <button type="button" onClick={() => onOpenSource(s.chunk_id)}>
                <span className="source-head">
                  <span className={`source-n${cited ? '' : ' is-uncited'}`}>{s.n}</span>
                  <span className="source-speaker">
                    {s.speaker}
                    {s.role ? ` ${s.role}` : ''}
                  </span>
                  <span className={`cited-badge${cited ? '' : ' is-uncited'}`}>
                    {cited ? '인용됨' : '미인용'}
                  </span>
                </span>
                <span className="source-meta">
                  {s.party ? `${s.party} · ` : ''}
                  {s.committee} · {s.date} · p.{s.page_start}
                </span>
                <span className="source-snippet">{s.snippet}</span>
                <span className="source-open">원문 보기 →</span>
              </button>
            </li>
          )
        })}
        <li className="source-note">전달된 근거 중 인용되지 않은 항목도 함께 표시합니다.</li>
      </ul>
    </div>
  )
}

export default SourcePanel
