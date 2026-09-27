import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { api, setCsrf } from './api'
import './style.css'
import { Library } from './Library'
import { RetentionSettings } from './RetentionSettings'
import { DiagnosticsPanel } from './DiagnosticsPanel'
import { confirmNavigation } from './unsaved'

type Session = { csrf_token: string; owner: boolean }
let opening: Promise<Session> | null = null
function openSession() {
  opening ||= api<Session>('/auth/local', { method: 'POST' }).finally(() => { opening = null })
  return opening
}
const notifyAuth = () => {
  const channel = new BroadcastChannel('minutes-auth')
  channel.postMessage('changed'); channel.close()
}

function App() {
  const [ready, setReady] = useState(false)
  const [owner, setOwner] = useState(false)
  const [loading, setLoading] = useState(false)
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [tab, setTab] = useState<'meetings' | 'videos' | 'settings'>(['#new-video', '#new-youtube'].includes(location.hash) ? 'videos' : 'meetings')
  useEffect(() => {
    let stopped = false
    const update = (result: Session) => { if (!stopped) { setCsrf(result.csrf_token); setOwner(result.owner); setReady(true) } }
    const refresh = () => { void api<Session>('/auth/session').catch(() => openSession()).then(update).catch(e => { if (!stopped) setError(e.message) }) }
    void openSession().then(update).catch(e => { if (!stopped) setError(e.message) })
    const channel = new BroadcastChannel('minutes-auth')
    channel.onmessage = refresh
    const expire = () => setOwner(false)
    window.addEventListener('focus', refresh)
    window.addEventListener('minutes-owner-expired', expire)
    const timer = setInterval(refresh, 30000)
    return () => { stopped = true; channel.close(); clearInterval(timer); window.removeEventListener('focus', refresh); window.removeEventListener('minutes-owner-expired', expire) }
  }, [])
  async function login(event: React.FormEvent) {
    event.preventDefault(); setError(''); setLoading(true)
    try {
      const result = await api<Session>('/auth/login', { method: 'POST', body: JSON.stringify({ key }) })
      setCsrf(result.csrf_token); setKey(''); setOwner(true); notifyAuth()
    } catch (e) { setError((e as Error).message) } finally { setLoading(false) }
  }
  async function logout() {
    setError('')
    try {
      const result = await api<Session>('/auth/logout', { method: 'POST' })
      setOwner(false); setKey(''); setCsrf(result.csrf_token); notifyAuth()
    } catch (e) { setError((e as Error).message) }
  }
  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">m.</span><div>회의 노트<small>LOCAL MINUTES</small></div></div>
      <nav aria-label="주 메뉴">
        <button className={tab === 'meetings' ? 'active' : ''} onClick={async () => { if (tab === 'meetings' || await confirmNavigation()) setTab('meetings') }}><span>▤</span> 회의록</button>
        <button className={tab === 'videos' ? 'active' : ''} onClick={async () => { if (tab === 'videos' || await confirmNavigation()) setTab('videos') }}><span aria-hidden="true">▷</span> 영상 요약</button>
        <button className={tab === 'settings' ? 'active' : ''} onClick={async () => { if (tab === 'settings' || await confirmNavigation()) setTab('settings') }}><span>⚙</span> 설정 및 진단</button>
      </nav>
      <div className="privacy"><span className="status-dot"/> 내 컴퓨터에서 전사<small>음성 파일은 외부로 보내지 않습니다.</small></div>
    </aside>
    <main>
      <header><span>나의 작업 공간</span><div className="header-actions"><span className="local-badge">LOCAL · CPU</span>{owner && <button className="secondary" onClick={() => void logout()}>로그아웃</button>}</div></header>
      <section className="page">
        {!ready ? <p role="status">로컬 작업 공간을 여는 중입니다.</p> : tab === 'meetings' ? <Library key='meetings' /> : tab === 'videos' ? <Library key='videos' kind='video_summary' /> : owner ? <>
          <span className="eyebrow">WORKSPACE SETTINGS</span><h1>설정 및 진단</h1>
          <RetentionSettings /><DiagnosticsPanel />
        </> : <div className="login-card"><h1>설정 로그인</h1><p>설정과 진단은 이 컴퓨터의 소유자 키로 엽니다. 일반 회의 작업은 키 없이 사용할 수 있습니다.</p>
          <form onSubmit={login}><label htmlFor="owner-key">소유자 키</label><input id="owner-key" type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} required />
            <button className="primary" disabled={loading || !key}>{loading ? '확인 중…' : '설정 열기'}</button></form>
        </div>}
        {error && <p role="alert" className="error">{error}</p>}
      </section>
      <footer>LOCAL MINUTES <span>기록은 로컬에. 다음 행동은 명확하게.</span></footer>
    </main>
  </div>
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
