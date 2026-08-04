import { useEffect, useState } from 'react'

// 질의 대기 표시 — 백엔드 스트리밍이 없으므로 시간 기반 단계 전환이 정직한 최선.
// 단계 시점(0/3/6초)은 qa 실측 지연(3~5초)에 맞춘 값. report 는 마지막 단계에 오래 머문다.
const STAGES = [
  { at: 0, label: '근거 검색 중…' },
  { at: 3, label: '근거 정리 중…' },
  { at: 6, label: '답변 작성 중…' },
]

export default function QueryProgress() {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const t0 = Date.now()
    // 100ms 폴링: 소수 첫째 자리까지 표시하므로 500ms 면 .0/.5 로만 튄다
    const id = setInterval(() => setElapsed((Date.now() - t0) / 1000), 100)
    return () => clearInterval(id)
  }, [])

  const stage = [...STAGES].reverse().find(s => elapsed >= s.at) ?? STAGES[0]

  return (
    <div className="query-progress" role="status" aria-live="polite">
      <span className="query-progress-spinner" aria-hidden="true" />
      <span className="query-progress-stage">{stage.label}</span>
      {/* 숫자만 mono — 한글이 섞인 문자열에 mono 를 쓰면 자간이 벌어진다 */}
      <span className="query-progress-elapsed">
        경과 <span className="num">{elapsed.toFixed(1)}</span>초
      </span>
    </div>
  )
}
