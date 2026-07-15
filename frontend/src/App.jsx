import { useEffect, useState } from 'react'
import './App.css'
import { postQuery, pingHealth, fetchMe, getToken, clearToken } from './api'
import QueryForm from './components/QueryForm'
import AnswerPanel from './components/AnswerPanel'
import SourcePanel from './components/SourcePanel'
import SourceModal from './components/SourceModal'
import IssueView from './components/IssueView'
import ActorView from './components/ActorView'
import AuthModal from './components/AuthModal'
import MyQueries from './components/MyQueries'
import Hero from './components/Hero'

// URL 쿼리 파라미터 ↔ 화면 상태 (공유 가능 링크: ?tab=issues&issue=medical-reform)
const TABS = ['query', 'issues', 'actor']

function readUrlState() {
  const p = new URLSearchParams(window.location.search)
  return {
    tab: TABS.includes(p.get('tab')) ? p.get('tab') : 'query',
    issue: p.get('issue'),
    actor: p.get('actor'),
  }
}

function urlFromState(tab, issue, actor) {
  const p = new URLSearchParams()
  if (tab !== 'query') p.set('tab', tab)
  if (tab === 'issues' && issue) p.set('issue', issue)
  if (tab === 'actor' && actor) p.set('actor', actor)
  const s = p.toString()
  return s ? `?${s}` : window.location.pathname
}

function App() {
  const initialUrl = readUrlState()
  const [tab, setTab] = useState(initialUrl.tab)
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState('qa')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [highlightN, setHighlightN] = useState(null) // [n] 클릭 시 하이라이트할 출처 번호
  const [modalChunkId, setModalChunkId] = useState(null)
  const [selectedActor, setSelectedActor] = useState(initialUrl.actor)
  const [selectedIssue, setSelectedIssue] = useState(initialUrl.issue)
  const [serverStatus, setServerStatus] = useState('checking') // checking | ok | down
  const [user, setUser] = useState(null)         // {username} | null
  const [authOpen, setAuthOpen] = useState(false)

  // 상태 → URL 반영 (replaceState — 히스토리 오염 없이 현재 화면을 공유 가능하게)
  useEffect(() => {
    const url = urlFromState(tab, selectedIssue, selectedActor)
    if (url !== window.location.pathname + window.location.search) {
      window.history.replaceState(null, '', url)
    }
  }, [tab, selectedIssue, selectedActor])

  useEffect(() => {
    pingHealth().then(() => setServerStatus('ok')).catch(() => setServerStatus('down'))
  }, [])

  useEffect(() => {
    if (!getToken()) return
    fetchMe().then(me => setUser(me)).catch(e => {
      // 401(무효 토큰)만 삭제 — 콜드스타트 타임아웃·네트워크 실패로 유효 토큰을 지우지 않는다
      if (e.status === 401) clearToken()
    })
  }, [])

  function logout() { clearToken(); setUser(null) }

  function openActor(name) { setSelectedActor(name); setTab('actor') }
  function openIssue(issueId) { setSelectedIssue(issueId); setTab('issues') }

  async function handleSubmit(q = question) {
    if (!q.trim() || loading) return
    setLoading(true)
    setResult(null)
    setError(null)
    setHighlightN(null)
    try {
      setResult(await postQuery(q, mode))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  // 첫 방문자용 예시 질문 — 클릭 즉시 실행 (질의·입장·쟁점 유형을 하나씩)
  const EXAMPLES = [
    '의대 정원 확대에 대한 여야 입장은?',
    '12·3 비상계엄 이후 국회 논의는?',
    '전세사기 피해자 지원 대책은?',
    'AI 기본법의 핵심 쟁점은?',
  ]

  function askExample(q) {
    setQuestion(q)
    handleSubmit(q)
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
        <div className="auth-corner">
          {user ? (
            <>
              <span>{user.username}님</span>
              <button type="button" onClick={logout}>로그아웃</button>
            </>
          ) : (
            <button type="button" onClick={() => setAuthOpen(true)}>로그인 / 가입</button>
          )}
        </div>
      </header>

      {serverStatus === 'checking' && (
        <div style={{ background: 'var(--warning-soft)', color: 'var(--warning)', padding: '8px 12px', borderRadius: 'var(--radius)', marginBottom: 12, fontSize: 14 }}>
          무료 서버를 깨우는 중입니다 (최대 1분)…
        </div>
      )}
      {serverStatus === 'down' && (
        <div style={{ background: 'var(--danger-soft)', color: 'var(--danger)', padding: '8px 12px', borderRadius: 'var(--radius)', marginBottom: 12, fontSize: 14 }}>
          서버 연결 실패 — 잠시 후 새로고침해주세요.
        </div>
      )}

      <nav className="tab-nav" aria-label="주요 화면">
        {[
          ['query', '질의'],
          ['issues', '쟁점 분석'],
          ['actor', '의원 프로필'],
        ].map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? 'active' : ''}
            aria-current={tab === id ? 'page' : undefined}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>

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

            <MyQueries user={user} onPick={q => setQuestion(q)} />

            {!result && !loading && (
              <>
                <Hero />
                <div className="example-chips">
                  <span className="chips-label">이런 질문을 해보세요</span>
                  {EXAMPLES.map((q) => (
                    <button key={q} type="button" onClick={() => askExample(q)}>
                      {q}
                    </button>
                  ))}
                </div>
              </>
            )}

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

      {tab === 'issues' && (
        <IssueView selectedIssue={selectedIssue} onActorClick={openActor} onSelChange={setSelectedIssue} />
      )}
      {tab === 'actor' && (
        <ActorView actor={selectedActor} onIssueClick={openIssue} onShown={setSelectedActor} />
      )}

      {authOpen && (
        <AuthModal onClose={() => setAuthOpen(false)} onSuccess={name => setUser({ username: name })} />
      )}

      <footer>
        <small>
          22대 국회 상임위 회의록 767건 (2024-05 ~ 2026-06) &nbsp;|&nbsp;
          근거가 부족한 내용은 확인 불가로 안내합니다.
          <br />
          데모 데이터: 24개 쟁점 관련 발언 모음 (전체 발언 기록 42만 건 중 일부) — 의원
          프로필 통계도 이 기준입니다.
        </small>
      </footer>
    </div>
  )
}

export default App
