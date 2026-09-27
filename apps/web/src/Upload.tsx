import { BackButton } from './BackButton'
import { useRef, useState } from 'react'
import { api, uploadFile } from './api'
import type { Meeting } from './Library'
import { useUnsaved } from './unsaved'

const storageKey = 'localminutes-pending-upload'
type Pending = { key: string; meeting?: string; title: string; date: string; fileName: string; fileSize: number; lastModified: number }
function savedUpload(key: string): Pending | null {
  try { return JSON.parse(sessionStorage.getItem(key) || 'null') as Pending | null } catch { return null }
}
export function kstNow() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date())
  const value = (name: string) => parts.find(part => part.type === name)!.value
  return `${value('year')}-${value('month')}-${value('day')}T${value('hour')}:${value('minute')}`
}

export function Upload({ kind = 'meeting', busy, setBusy, done, close }: { kind?: 'meeting' | 'video_summary'; busy: boolean; setBusy: (value: boolean) => void; done: (meeting: Meeting) => void; close: () => void }) {
  const video = kind === 'video_summary'
  const key = storageKey + (video ? '-video' : '')
  const pending = useRef<Pending | null>(savedUpload(key))
  const [title, setTitle] = useState(pending.current?.title || '')
  const [date, setDate] = useState(pending.current?.date || kstNow())
  const [file, setFile] = useState<File | null>(null)
  const [progress, setProgress] = useState(0)
  const [transferred, setTransferred] = useState(0)
  const [error, setError] = useState('')
  useUnsaved(busy || !!file || !!title)
  function choose(value: File | null) {
    if (busy) return
    const existing = pending.current
    if (value && existing && (value.name !== existing.fileName || value.size !== existing.fileSize || value.lastModified !== existing.lastModified)) {
      setError(`진행 중이던 ${existing.fileName} 파일을 다시 선택하세요.`); return
    }
    setFile(value); setError('')
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!file) return
    if (file.size > 2_000_000_000) { setError('파일은 2GB 이하로 선택하세요.'); return }
    setBusy(true); setError('')
    try {
      if (!pending.current) {
        pending.current = { key: crypto.randomUUID(), title, date, fileName: file.name, fileSize: file.size, lastModified: file.lastModified }
        sessionStorage.setItem(key, JSON.stringify(pending.current))
      }
      const selected = pending.current
      if (!selected.meeting) {
        const meeting = await api<Meeting>(video ? '/videos' : '/meetings', { method: 'POST', headers: { 'Idempotency-Key': selected.key }, body: JSON.stringify({
          title: selected.title, language: 'auto', speakers: null, occurred_at: video ? null : `${selected.date}:00+09:00`, timezone: 'Asia/Seoul', allow_external_text: true,
        }) })
        selected.meeting = meeting.id
        sessionStorage.setItem(key, JSON.stringify(selected))
      }
      await uploadFile(selected.meeting, file, selected.key, (percent, loaded) => { setProgress(percent); setTransferred(loaded) })
      const meeting = await api<Meeting>(`/meetings/${selected.meeting}`)
      sessionStorage.removeItem(key); pending.current = null
      done(meeting)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <form className="settings-card upload-form" onSubmit={submit} onDragOver={event => event.preventDefault()} onDrop={event => {
    event.preventDefault()
    if (event.dataTransfer.files.length !== 1) { setError('파일은 한 번에 하나만 올려주세요.'); return }
    choose(event.dataTransfer.files[0])
  }}><div className="panel-heading"><h1>{video ? '새 영상 요약' : '새 회의'}</h1><BackButton disabled={busy} onClick={close} /></div>
    <fieldset disabled={busy}>
      <label>{video ? '영상 제목' : '회의 제목'}<input placeholder="비워두면 AI가 제목을 정합니다" maxLength={200} disabled={!!pending.current} value={title} onChange={e => setTitle(e.target.value)} /></label>
      <label>음성 또는 영상 파일<input type="file" required={!file} accept=".wav,.mp3,.m4a,.mp4,.mov,.webm,.mkv,.ogg,.flac" onChange={e => choose(e.target.files?.[0] || null)} /></label>
      <p>파일을 선택하거나 이 영역에 끌어 놓으세요. {file ? `선택: ${file.name}` : pending.current ? `이전 입력 이어서 진행: ${pending.current.fileName}` : ''}</p>
      <p>파일 1개 · 최대 2GB · 최대 120분. 영상에서는 음성만 처리합니다.</p>
      {!video && <label>회의 일시 (한국 시간)<input required type="datetime-local" disabled={!!pending.current} value={date} onChange={e => setDate(e.target.value)} /></label>}
      <label className="checkbox"><input type="checkbox" checked disabled readOnly />{video ? '연결된 AI를 사용한 요약본 생성' : '연결된 AI를 사용한 회의록 생성'}</label>
    </fieldset>
    {busy && <div role="status"><progress value={progress} max={100} /> 업로드 {progress}% · {transferred.toLocaleString()} / {file?.size.toLocaleString()} 바이트{progress === 100 ? ' · 서버 저장 확인 중' : ''}</div>}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="toolbar submit-actions"><button className="primary" disabled={busy || !file}>{busy ? '업로드 중…' : pending.current ? '같은 업로드 재시도' : '업로드하고 처리 시작'}</button></div>
  </form>
}
