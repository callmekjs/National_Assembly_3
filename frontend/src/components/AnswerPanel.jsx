import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { postFeedback } from '../api'

// 근거 상태 카드 — 작은 배지 대신 큰 수치로 신뢰도를 앞세운다.
// 값 자체(FULL 등)는 API 계약이라 그대로 두고 표기만 바꾼다.
const GROUNDING_CARD = {
  FULL: { tone: 'is-ok', title: '근거 확인 완료', desc: '답변 문장 전부가 인용 근거에 연결되었습니다' },
  PARTIAL: { tone: 'is-warn', title: '일부만 근거 확인됨', desc: '인용 근거에 연결되지 않은 문장이 있습니다' },
  REFUSED: { tone: 'is-danger', title: '기록에서 확인 불가', desc: '회의록에서 근거를 찾지 못해 답변을 보류했습니다' },
  NONE: { tone: 'is-none', title: '근거 연결 없음', desc: '이 답변에 연결된 인용 근거가 없습니다' },
}

// 검증층 flag 한국어 라벨 (spec 결정 ② (a) — 텍스트 불변 + 강등 + 배지)
const VERIFICATION_LABEL = {
  comparison_one_sided: '한쪽 진영 근거만으로 비교됨',
  speaker_both_sides: '같은 발언자가 양쪽 진영에 배치됨',
  qa_pairing_date_mismatch: '질문·답변 인용이 서로 다른 회의',
  speaker_role_mismatch: '발언자·기관 귀속 불일치',
  party_label_mismatch: '정당 표기가 근거와 다름',
  keyword_missing: '핵심 대상이 인용 근거에 없음',
  ruling_period_mismatch: '발언 시점과 정권 시기 불일치',
  // 다른 flag 는 "이런 문제를 찾았다"지만 이것은 "검사를 못 끝냈다"는 뜻이다 —
  // 답변의 결함이 아니라 검증층의 결함이므로 문구를 구분한다 (감사 2026-08-05)
  verification_incomplete: '검증 규칙 일부가 실행되지 못함 (답변 결함 아님)',
}

// 근거 카드의 큰 수치 = (인용이 달린 문장 / 전체 문장). 마크다운 제목 줄은 문장이 아니다.
function sentenceStats(answer) {
  const body = (answer || '')
    .split('\n')
    .filter((l) => !/^\s*#{1,6}\s/.test(l))
    .join('\n')
  const sentences = body
    .split(/(?<=[.!?])\s+|\n+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 1)
  return {
    total: sentences.length,
    cited: sentences.filter((s) => /\[\d+\]/.test(s)).length,
  }
}

function groundingFigure(grounding, { total, cited }) {
  if (!total || grounding === 'NONE') return '—'
  if (grounding === 'FULL') return `${total}/${total}`
  if (grounding === 'REFUSED') return `0/${total}`
  return `${cited}/${total}`
}

// 텍스트 속 [n]을 클릭 가능한 인용 버튼으로 치환 (표시는 대괄호 없이 숫자만)
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
          {n}
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
  const card = GROUNDING_CARD[result.grounding] ?? GROUNDING_CARD.NONE
  const stats = sentenceStats(result.answer)
  const flags = result.verification?.flags ?? []
  const citedCount = result.cited_numbers?.length ?? 0
  const sourceCount = result.sources?.length ?? 0

  return (
    <div className="answer-panel">
      {result.ungrounded && (
        <div className="ungrounded-banner">
          ⚠ 이 답변에는 출처가 연결되지 않은 내용이 있습니다
        </div>
      )}

      <div className="status-cards">
        <div className={`status-card ${card.tone}`}>
          <div className="status-card-figure">{groundingFigure(result.grounding, stats)}</div>
          <div className="status-card-body">
            <div className="status-card-title">{card.title}</div>
            <div className="status-card-desc">{card.desc}</div>
          </div>
        </div>

        {/* 검증 flag 0건이면 카드를 그리지 않는다 — 근거 카드가 전체 폭을 쓴다 */}
        {flags.length > 0 && (
          <div className="status-card is-warn">
            <div className="status-card-figure">{flags.length}</div>
            <div className="status-card-body">
              <div className="status-card-title">자동 검증 주의</div>
              <div className="status-card-desc">
                {flags.map((f) => VERIFICATION_LABEL[f] ?? f).join(' · ')}
              </div>
            </div>
          </div>
        )}
      </div>

      {result.issue_context && (
        <div className="issue-context-note">
          이슈 분석 반영: {result.issue_context.title}
        </div>
      )}

      <div className="answer-card">
        <div className="answer-markdown">
          <ReactMarkdown components={components}>{result.answer}</ReactMarkdown>
        </div>

        <div className="answer-meta">
          <span>
            {result.mode === 'report' ? '정책 브리핑' : '간단 답변'} ·{' '}
            <span className="num">{seconds}</span>초 · 인용{' '}
            <span className="num">{citedCount}</span>건 / 전달{' '}
            <span className="num">{sourceCount}</span>건
          </span>
          {result.query_id && (
            <span className="feedback">
              {feedback ? (
                '평가 감사합니다'
              ) : (
                <>
                  <span>이 답변이 도움이 되었습니까</span>
                  <button type="button" onClick={() => handleFeedback('up')}>예</button>
                  <button type="button" onClick={() => handleFeedback('down')}>아니오</button>
                </>
              )}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

export default AnswerPanel
