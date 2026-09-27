import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { api, setCsrf } from './api'
import './style.css'
import { Library } from './Library'
import { RetentionSettings } from './RetentionSettings'
import { DiagnosticsPanel } from './DiagnosticsPanel'
import { confirmNavigation } from './unsaved'


function App() {
  const [ready, setReady] = useState(false)
  const [loading, setLoading] = useState(true)
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [tab, setTab] = useState<'meetings' | 'settings'>('meetings')
  useEffect(() => {
    api<{ csrf_token: string }>('/auth/session').then(result => { setCsrf(result.csrf_token); setReady(true) })
      .catch(() => setReady(false)).finally(() => setLoading(false))
  }, [])
  async function login(event: React.FormEvent) {
    event.preventDefault(); setError(''); setLoading(true)
    try {
      const result = await api<{ csrf_token: string }>('/auth/login', { method: 'POST', body: JSON.stringify({ key }) })
      setCsrf(result.csrf_token); setKey(''); setReady(true)
    } catch (e) { setError((e as Error).message) } finally { setLoading(false) }
  }
  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">m.</span><div>회의 노트<small>LOCAL MINUTES</small></div></div>
      <nav aria-label="주 메뉴">
        <button className={tab === 'meetings' ? 'active' : ''} onClick={() => { if (tab === 'meetings' || confirmNavigation()) setTab('meetings') }}><span>▤</span> 내 회의</button>
        <button className={tab === 'settings' ? 'active' : ''} onClick={() => { if (tab === 'settings' || confirmNavigation()) setTab('settings') }}><span>⚙</span> 설정 및 진단</button>
      </nav>
      <div className="privacy"><span className="status-dot"/> 내 컴퓨터에서 전사<small>음성 파일은 외부로 보내지 않습니다.</small></div>
    </aside>
    <main>
      <header><span>나의 작업 공간</span><span className="local-badge">LOCAL · CPU</span></header>
      <section className="page">
        {!ready ? <div className="login-card">
          <span className="eyebrow">나만의 회의 기록</span><h1>내 작업 공간 열기</h1>
          <p>이 컴퓨터의 소유자 키로 접속하세요.<br/>키는 서버의 로컬 설정 폴더에 보관됩니다.</p>
          <form onSubmit={login}><label htmlFor="owner-key">소유자 키</label><input id="owner-key" type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} required />
            <button className="primary" disabled={loading || !key}>{loading ? '확인 중…' : '작업 공간 열기 →'}</button></form>
          <p className="helper">서버 사용자 설정 폴더의 <code>owner-key</code> 파일에서 확인할 수 있습니다. 키를 다른 사람과 공유하지 마세요.</p>
        </div> : tab === 'meetings' ? <Library /> : <><span className="eyebrow">WORKSPACE SETTINGS</span><h1>설정 및 진단</h1><p>로컬 실행 환경의 연결 상태입니다.</p>
          <DiagnosticsPanel />
          <RetentionSettings />
          <button className="secondary" onClick={async () => { try { await api('/auth/logout', { method: 'POST' }); setReady(false); setCsrf('') } catch (e) { setError((e as Error).message) } }}>로그아웃</button>
        </>}
        {error && <p role="alert" className="error">{error}</p>}
      </section>
      <footer>LOCAL MINUTES <span>기록은 로컬에. 다음 행동은 명확하게.</span></footer>
    </main>
  </div>
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
