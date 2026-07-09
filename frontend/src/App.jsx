import { useState } from 'react'
import './App.css'
import { postQuery } from './api'
import QueryForm from './components/QueryForm'
import AnswerPanel from './components/AnswerPanel'
import SourcePanel from './components/SourcePanel'
import SourceModal from './components/SourceModal'
import IssueView from './components/IssueView'

function App() {
  const [tab, setTab] = useState('query')
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState('qa')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [highlightN, setHighlightN] = useState(null) // [n] 클릭 시 하이라이트할 출처 번호
  const [modalChunkId, setModalChunkId] = useState(null)

  async function handleSubmit() {
    if (!question.trim() || loading) return
    setLoading(true)
    setResult(null)
    setError(null)
    setHighlightN(null)
    try {
      setResult(await postQuery(question, mode))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function handleCiteClick(n) {
    setHighlightN(n)
    document
      .getElementById(`source-${n}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  return (
    <div className="container">
      <header>
        <h1>국회 회의록 RAG</h1>
        <p className="subtitle">
          국회 회의록을 근거로 정책 의제, 행위자, 쟁점, 입장 차이, 시계열 흐름을 분석하는 GovTech RAG 서비스
        </p>
      </header>

      <div style={{ marginBottom: 12 }}>
        <button onClick={() => setTab('query')} disabled={tab === 'query'}>질의</button>
        <button onClick={() => setTab('issues')} disabled={tab === 'issues'}>쟁점 분석</button>
      </div>

      {tab === 'query' && (
        <>
          <main>
            <QueryForm
              question={question}
              setQuestion={setQuestion}
              mode={mode}
              setMode={setMode}
              loading={loading}
              onSubmit={handleSubmit}
            />

            {error && <div className="error">{error}</div>}

            {result && (
              <div className="result-grid">
                <AnswerPanel key={result.query_id ?? 'no-log'} result={result} onCiteClick={handleCiteClick} />
                <SourcePanel
                  sources={result.sources}
                  citedNumbers={result.cited_numbers}
                  highlightN={highlightN}
                  onOpenSource={setModalChunkId}
                />
              </div>
            )}
          </main>

          {modalChunkId && (
            <SourceModal chunkId={modalChunkId} onClose={() => setModalChunkId(null)} />
          )}
        </>
      )}

      {tab === 'issues' && <IssueView />}

      <footer>
        <small>
          22대 국회 상임위 회의록 767건 (2024-05 ~ 2026-06) &nbsp;|&nbsp;
          근거가 부족한 내용은 확인 불가로 안내합니다.
        </small>
      </footer>
    </div>
  )
}

export default App
