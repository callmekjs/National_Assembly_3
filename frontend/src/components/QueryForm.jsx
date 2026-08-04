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

  return (
    <form
      className="query-form"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit()
      }}
    >
      <div className="query-input-row">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="질문을 입력하세요. 예: AI 기본법 논의의 핵심 쟁점은 무엇인가?"
          rows={2}
        />
        <button type="submit" className="query-submit" disabled={loading || !question.trim()}>
          {loading ? '생성 중…' : '질문하기'}
        </button>
      </div>

      <div className="query-form-meta">
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
        <span className="form-hint">Enter로 제출 · Shift+Enter로 줄바꿈</span>
        {/* 대기 중에도 유지 — 소요 시간 고지는 기다리는 동안 가장 필요하다 */}
        {mode === 'report' && (
          <span className="form-hint">
            정책 브리핑은 보통 10~20초 걸립니다 — 여러 근거를 구조화해 정리합니다.
          </span>
        )}
      </div>
    </form>
  )
}

export default QueryForm
