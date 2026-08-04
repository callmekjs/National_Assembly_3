// 월별 발언 수 막대 차트 — IssueView 타임라인 / ActorView 프로필 공용.
// 독자의 질문은 "언제 뜨거웠나" 하나라서 절대값 막대 + 피크 강조만 그린다.
// SVG 좌표 계산 대신 flex 막대 — 라벨이 실제 텍스트라 폰트 토큰·줄바꿈 규칙을 그대로 탄다.

// '2024-09' → '24-09' (축 라벨은 mono, 연도 2자리로 폭 절약)
function axisLabel(month) {
  const m = String(month || '')
  return /^\d{4}-\d{2}/.test(m) ? m.slice(2, 7) : m
}

export default function MonthlyBars({
  months,
  unit = '건',
  ariaLabel = '월별 발언 수 막대 차트',
  height = 170,
  barMax = 34,
  gap = 10,
}) {
  if (!months || months.length === 0) return <p className="chart-empty">타임라인 데이터 없음</p>

  const n = months.length
  const vals = months.map((m) => m.value || 0)
  const max = Math.max(...vals, 1)
  const peakIdx = vals.indexOf(Math.max(...vals))

  // 막대가 많으면 수치 라벨이 겹친다 — 피크만 남긴다
  const showAllValues = n <= 16
  // 축 라벨도 최대 12개까지만 (첫·끝·피크는 항상 유지)
  const step = Math.max(Math.ceil(n / 12), 1)
  const showAxis = (i) => i % step === 0 || i === n - 1 || i === peakIdx

  const vars = { '--bars-height': `${height}px`, '--bars-max': `${barMax}px`, '--bars-gap': `${gap}px` }

  return (
    <div style={vars} role="img" aria-label={ariaLabel}>
      <div className="bars">
        {months.map((m, i) => (
          <div
            key={m.month}
            className={`bars-col${i === peakIdx && vals[i] > 0 ? ' is-peak' : ''}`}
            title={`${m.month} · ${vals[i]}${unit}`}
          >
            {(showAllValues || i === peakIdx) && vals[i] > 0 && (
              <span className="bars-value">{vals[i]}</span>
            )}
            <span
              className="bars-bar"
              style={{ height: `${vals[i] > 0 ? Math.max((vals[i] / max) * 100, 1) : 0}%` }}
            />
          </div>
        ))}
      </div>
      <div className="bars-axis" aria-hidden="true">
        {months.map((m, i) => (
          <span key={m.month} className={i === peakIdx ? 'is-peak' : undefined}>
            {showAxis(i) ? axisLabel(m.month) : ''}
          </span>
        ))}
      </div>
    </div>
  )
}
