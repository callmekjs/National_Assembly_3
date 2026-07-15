import { useEffect, useState } from 'react'

// 질의 대기 표시 — 백엔드 스트리밍이 없으므로 시간 기반 단계 전환이 정직한 최선.
// 단계 시점(0/3/6초)은 qa 실측 지연(3~5초)에 맞춘 값. report 는 마지막 단계에 오래 머문다.
const STAGES = [
  { at: 0, label: '근거 검색 중…' },
  { at: 3, label: '근거 정리 중…' },
  { at: 6, label: '답변 작성 중…' },
]

export default function QueryProgress({ mode }) {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const t0 = Date.now()
    // 500ms 폴링: 1s 인터벌은 탭 스로틀링 시 초 표시를 건너뛴다
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 500)
    return () => clearInterval(id)
  }, [])

  const stage = [...STAGES].reverse().find(s => elapsed >= s.at) ?? STAGES[0]

  return (
    <div className="query-progress" role="status" aria-live="polite">
      <span className="query-progress-spinner" aria-hidden="true" />
      <span className="query-progress-stage">{stage.label}</span>
      <span className="query-progress-elapsed">
        {elapsed}초 경과{mode === 'report' ? ' · 보통 10~20초' : ''}
      </span>
    </div>
  )
}
