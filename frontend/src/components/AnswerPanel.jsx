import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { postFeedback } from '../api'

const GROUNDING_CLASS = {
  FULL: 'grounding-full',
  PARTIAL: 'grounding-partial',
  REFUSED: 'grounding-refused',
  NONE: 'grounding-none',
}

// 텍스트 속 [n]을 클릭 가능한 인용 버튼으로 치환
function withCitations(children, onCiteClick) {
  return (Array.isArray(children) ? children : [children]).flatMap((child, i) => {
    if (typeof child !== 'string') return [child]
    const parts = child.split(/(\[\d+\])/)
    return parts.map((part, j) => {
      const m = part.match(/^\[(\d+)\]$/)
      if (!m) return part
      const n = Number(m[1])
      return (
        <button
          key={`cite-${i}-${j}`}
          type="button"
          className="cite-ref"
          onClick={() => onCiteClick(n)}
          title={`출처 [${n}] 보기`}
        >
          [{n}]
        </button>
      )
    })
  })
}

function AnswerPanel({ result, onCiteClick }) {
  const [feedback, setFeedback] = useState(null) // null | 'up' | 'down'

  async function handleFeedback(kind) {
    if (feedback) return
    try {
      await postFeedback(result.query_id, kind === 'up' ? 5 : 1)
      setFeedback(kind)
    } catch {
      // 피드백 실패는 답변 열람을 막지 않는다 — 조용히 무시
    }
  }

  // Markdown 요소마다 자식 텍스트의 [n]을 인용 버튼으로 바꾼다
  const cite = (Tag) =>
    function CiteElement({ children, ...props }) {
      return <Tag {...props}>{withCitations(children, onCiteClick)}</Tag>
    }
  const components = { p: cite('p'), li: cite('li'), strong: cite('strong'), em: cite('em') }

  const seconds = (result.latency_ms / 1000).toFixed(1)
  const cost = result.usage?.est_cost_usd

  return (
    <div className="answer-panel">
      <span className={`grounding-badge ${GROUNDING_CLASS[result.grounding] ?? 'grounding-none'}`}>
        Grounding: {result.grounding}
      </span>

      {result.issue_context && (
        <div style={{ fontSize: 12, color: '#2563eb', margin: '4px 0' }}>
          📊 이슈 분석 반영: {result.issue_context.title}
        </div>
      )}

      {result.ungrounded && (
        <div className="ungrounded-banner">⚠ 이 답변에는 출처가 연결되지 않은 내용이 있습니다</div>
      )}

      <div className="answer-markdown">
        <ReactMarkdown components={components}>{result.answer}</ReactMarkdown>
      </div>

      <div className="answer-meta">
        <span>
          {result.mode === 'report' ? '정책 브리핑' : '간단 답변'} · {seconds}초
          {cost != null && ` · $${cost}`}
        </span>
        {result.query_id && (
          <span className="feedback">
            {feedback ? (
              '평가 감사합니다'
            ) : (
              <>
                <button type="button" onClick={() => handleFeedback('up')} title="도움이 됐어요">
                  👍
                </button>
                <button type="button" onClick={() => handleFeedback('down')} title="아쉬워요">
                  👎
                </button>
              </>
            )}
          </span>
        )}
      </div>
    </div>
  )
}

export default AnswerPanel
