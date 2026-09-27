import { BackButton } from './BackButton'
import { useRef, useState } from 'react'
import { api } from './api'
import type { Meeting } from './Library'
import { useUnsaved } from './unsaved'
import { jobError } from './job-error'

const key = 'localminutes-pending-youtube'
type Pending = { url: string; title: string; key: string }
function saved(): Pending | null {
  try { return JSON.parse(sessionStorage.getItem(key) || 'null') } catch { return null }
}
export function YoutubeInput({ done, close }: { done: (meeting: Meeting) => void; close: () => void }) {
  const pending = useRef(saved())
  const [url, setUrl] = useState(pending.current?.url || '')
  const [title, setTitle] = useState(pending.current?.title || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useUnsaved(busy || !!url || !!title)
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      if (!pending.current || pending.current.url !== url || pending.current.title !== title) {
        pending.current = { url, title, key: crypto.randomUUID() }
        sessionStorage.setItem(key, JSON.stringify(pending.current))
      }
      const job = await api<{ meeting_id: string }>('/videos/youtube', { method: 'POST', headers: { 'Idempotency-Key': pending.current.key }, body: JSON.stringify({ url, title }) })
      const meeting = await api<Meeting>(`/meetings/${job.meeting_id}`)
      sessionStorage.removeItem(key); pending.current = null
      done(meeting)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <form className="settings-card upload-form" onSubmit={submit}><div className="panel-heading"><h1>YouTube 영상 요약</h1><BackButton disabled={busy} onClick={close} /></div>
    <fieldset disabled={busy}><label>YouTube 링크<input type="url" required maxLength={4096} value={url} onChange={e => setUrl(e.target.value)} /></label>
      <label>제목 (선택)<input maxLength={200} value={title} onChange={e => setTitle(e.target.value)} /></label>
      <p>재사용·처리가 허용된 단일 영상만 입력하세요. 최대 120분·2GB이며 진행 중 라이브, 로그인·유료 접근이 필요한 영상은 지원하지 않습니다.</p>
      <label className="checkbox"><input type="checkbox" checked disabled readOnly />연결된 AI를 사용한 요약본 생성</label>
    </fieldset>{error && <p role="alert" className="error">{jobError(error, true)}</p>}
    <div className="toolbar submit-actions"><button className="primary" disabled={busy || !url}>{busy ? '등록 중…' : '요약 작업 등록'}</button></div>
  </form>
}
