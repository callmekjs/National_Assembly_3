// 월별 발언 수 막대 차트 — IssueView 타임라인에서 추출한 공용 컴포넌트.
// 독자의 질문은 "언제 뜨거웠나" 하나라서 절대값 막대 + 피크 라벨 + 가장자리 눈금만 그린다.
export default function MonthlyBars({ months, unit = '건', ariaLabel = '월별 발언 수 막대 차트' }) {
  if (!months || months.length === 0) return <p>타임라인 데이터 없음</p>
  const W = 640, H = 190, padX = 30, padTop = 30, padBottom = 26
  const n = months.length
  const vals = months.map(m => m.value || 0)
  const max = Math.max(...vals, 1)
  const peakIdx = vals.indexOf(Math.max(...vals))
  const slot = (W - 2 * padX) / n
  const barW = Math.max(slot * 0.65, 2)
  const x = i => padX + i * slot + (slot - barW) / 2
  const y = v => padTop + (1 - v / max) * (H - padTop - padBottom)
  const step = Math.max(Math.ceil(n / 4), 1)
  const ticks = [...new Set([0, ...Array.from({ length: n }, (_, i) => i).filter(i => i % step === 0), n - 1])]
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ maxWidth: 720 }} role="img" aria-label={ariaLabel}>
      <line x1={padX} y1={H - padBottom} x2={W - padX} y2={H - padBottom} stroke="var(--ink-200)" />
      {months.map((m, i) => (
        <rect key={m.month} x={x(i)} y={y(vals[i])} width={barW}
              height={Math.max(H - padBottom - y(vals[i]), vals[i] > 0 ? 1.5 : 0)}
              fill={i === peakIdx ? 'var(--chart-bar-peak)' : 'var(--chart-bar)'} rx="1">
          <title>{`${m.month} · ${vals[i]}${unit}`}</title>
        </rect>
      ))}
      {vals[peakIdx] > 0 && (
        <text x={Math.min(Math.max(x(peakIdx) + barW / 2, 70), W - 70)} y={y(vals[peakIdx]) - 8}
              fontSize="12" fontWeight="600" fill="var(--chart-bar-peak)" textAnchor="middle">
          {months[peakIdx].month} · {vals[peakIdx]}{unit}
        </text>
      )}
      {ticks.map(i => (
        <text key={i} y={H - 8} fontSize="11" fill="var(--ink-700)"
              x={i === 0 ? padX : i === n - 1 ? W - padX : x(i) + barW / 2}
              textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}>
          {months[i].month}
        </text>
      ))}
    </svg>
  )
}
