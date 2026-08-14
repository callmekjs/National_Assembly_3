import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import AnswerPanel from './AnswerPanel'

function fullResult() {
  return {
    answer: '첫 번째 주장입니다 [1]. 두 번째 주장은 인용이 없습니다.',
    grounding: 'FULL',
    latency_ms: 1200,
    mode: 'qa',
    cited_numbers: [1],
    sources: [{ n: 1 }],
    verification: { flags: [] },
  }
}

describe('AnswerPanel 근거 카드', () => {
  it('FULL을 모든 문장이 검증됐다는 수치로 과장하지 않는다', () => {
    const html = renderToStaticMarkup(
      <AnswerPanel result={fullResult()} onCiteClick={() => {}} />,
    )

    expect(html).toContain('인용 근거 확인')
    expect(html).toContain('답변에 사용된 인용이 제공된 회의록 근거와 연결되어 있습니다')
    expect(html).not.toContain('답변 문장 전부')
    expect(html).not.toMatch(/\d+\/\d+/)
  })
})
