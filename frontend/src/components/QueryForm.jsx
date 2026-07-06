// 질문 입력 + 모드 토글. Enter 제출, Shift+Enter 줄바꿈.
function QueryForm({ question, setQuestion, mode, setMode, loading, onSubmit }) {
  function handleKeyDown(e) {
    // 한글 IME 조합 중 Enter 는 무시 — 조합 미완성 상태로 제출되는 것 방지
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSubmit()
    }
  }

  const loadingLabel = mode === 'report' ? '브리핑 생성 중... (약 15초)' : '검색 중... (약 5초)'

  return (
    <form
      className="query-form"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit()
      }}
    >
      <textarea
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="질문을 입력하세요. 예: AI 기본법 논의의 핵심 쟁점은 무엇인가?"
        rows={3}
      />
      <div className="form-actions">
        <div className="mode-toggle" role="group" aria-label="답변 모드">
          <button
            type="button"
            className={mode === 'qa' ? 'active' : ''}
            onClick={() => setMode('qa')}
          >
            간단 답변
          </button>
          <button
            type="button"
            className={mode === 'report' ? 'active' : ''}
            onClick={() => setMode('report')}
          >
            정책 브리핑
          </button>
        </div>
        <button type="submit" disabled={loading || !question.trim()}>
          {loading ? loadingLabel : '질문하기'}
        </button>
      </div>
    </form>
  )
}

export default QueryForm
