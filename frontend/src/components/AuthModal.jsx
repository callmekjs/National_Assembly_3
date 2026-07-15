import { useState } from 'react'
import { login, signup, setToken } from '../api'

// 로그인·가입 모달 — 성공 시 토큰 저장 후 onSuccess(username) 로 부모에 알린다
export default function AuthModal({ onClose, onSuccess }) {
  const [tab, setTab] = useState('login') // login | signup
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    if (busy) return
    setErr(null); setBusy(true)
    try {
      const fn = tab === 'login' ? login : signup
      const res = await fn(username.trim(), password)
      setToken(res.token)
      onSuccess(res.username)
      onClose()
    } catch (e2) { setErr(e2.message) } finally { setBusy(false) }
  }

  return (
    <div className="auth-overlay" onClick={onClose}>
      <div className="auth-modal" onClick={e => e.stopPropagation()} role="dialog" aria-label="로그인 또는 가입">
        <div className="auth-tabs">
          <button className={tab === 'login' ? 'active' : ''} onClick={() => { setTab('login'); setErr(null) }}>로그인</button>
          <button className={tab === 'signup' ? 'active' : ''} onClick={() => { setTab('signup'); setErr(null) }}>가입</button>
        </div>
        <form onSubmit={submit}>
          <input value={username} onChange={e => setUsername(e.target.value)}
                 placeholder="아이디 (영문·숫자·한글 2~20자)" autoFocus />
          <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                 placeholder="비밀번호 (8자 이상)" />
          {tab === 'signup' && (
            <p className="auth-notice">
              포트폴리오 데모 — 다른 곳에서 쓰는 비밀번호를 입력하지 마세요.
              계정과 기록은 예고 없이 초기화될 수 있습니다. (이메일 등 개인정보는 받지 않습니다)
            </p>
          )}
          {err && <p className="auth-error">{err}</p>}
          <button type="submit" disabled={busy || !username.trim() || !password}>
            {busy ? '처리 중…' : tab === 'login' ? '로그인' : '가입하기'}
          </button>
        </form>
      </div>
    </div>
  )
}
