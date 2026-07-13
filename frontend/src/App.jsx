import { useEffect, useState } from 'react'
import './App.css'
import { postQuery, pingHealth } from './api'
import QueryForm from './components/QueryForm'
import AnswerPanel from './components/AnswerPanel'
import SourcePanel from './components/SourcePanel'
import SourceModal from './components/SourceModal'
import IssueView from './components/IssueView'
import ActorView from './components/ActorView'

function App() {
  const [tab, setTab] = useState('query')
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState('qa')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [highlightN, setHighlightN] = useState(null) // [n] 클릭 시 하이라이트할 출처 번호
  const [modalChunkId, setModalChunkId] = useState(null)
  const [selectedActor, setSelectedActor] = useState(null)
  const [selectedIssue, setSelectedIssue] = useState(null)
  const [serverStatus, setServerStatus] = useState('checking') // checking | ok | down

  useEffect(() => {
    pingHealth().then(() => setServerStatus('ok')).catch(() => setServerStatus('down'))
  }, [])

  function openActor(name) { setSelectedActor(name); setTab('actor') }
  function openIssue(issueId) { setSelectedIssue(issueId); setTab('issues') }

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

      {serverStatus === 'checking' && (
        <div style={{ background: '#fef3c7', color: '#92400e', padding: '8px 12px', borderRadius: 6, marginBottom: 12, fontSize: 14 }}>
          무료 서버를 깨우는 중입니다 (최대 1분)…
        </div>
      )}
      {serverStatus === 'down' && (
        <div style={{ background: '#fee2e2', color: '#991b1b', padding: '8px 12px', borderRadius: 6, marginBottom: 12, fontSize: 14 }}>
          서버 연결 실패 — 잠시 후 새로고침해주세요.
        </div>
      )}

      <div style={{ marginBottom: 12 }}>
        <button onClick={() => setTab('query')} disabled={tab === 'query'}>질의</button>
        <button onClick={() => setTab('issues')} disabled={tab === 'issues'}>쟁점 분석</button>
        <button onClick={() => setTab('actor')} disabled={tab === 'actor'}>의원 프로필</button>
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

      {tab === 'issues' && <IssueView selectedIssue={selectedIssue} onActorClick={openActor} />}
      {tab === 'actor' && <ActorView actor={selectedActor} onIssueClick={openIssue} />}

      <footer>
        <small>
          22대 국회 상임위 회의록 767건 (2024-05 ~ 2026-06) &nbsp;|&nbsp;
          근거가 부족한 내용은 확인 불가로 안내합니다.
          <br />
          데모 코퍼스: 24개 쟁점 관련 발언 부분집합 (전체 42만 청크는 로컬 데모) — 의원
          프로필 통계도 이 부분집합 기준입니다.
        </small>
      </footer>
    </div>
  )
}

export default App
