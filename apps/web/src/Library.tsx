import { useEffect, useRef, useState } from 'react'
import { api, uploadFile } from './api'
import { MeetingDetail } from './MeetingDetail'
import { confirmNavigation, useUnsaved } from './unsaved'

export type Meeting = { id: string; title: string; created_at: number; settings_json: string; revision: number; transcript_version: string | null; minutes_revision: string | null; duration_ms?: number | null; minutes_status?: string | null }
type Job = { id: string; meeting_id: string; state: string; stage: string; queue_position: number | null; elapsed_seconds: number; blocked_reason: string | null; error_code: string | null; transcript_version: string | null }
type Media = { audio_available: boolean; media: { duration_ms: number | null; tracks: { index: number; language: string | null; codec: string }[] } | null }
const states: Record<string, string> = { QUEUED: '대기', RUNNING: '처리 중', BLOCKED: '확인 필요', INTERRUPTED: '중단됨', FAILED: '실패', CANCEL_REQUESTED: '취소 중', CANCELLED: '취소됨', COMPLETED: '완료', COMPLETED_TRANSCRIPT_ONLY: '전사 완료' }
const stages: Record<string, string> = { VALIDATE: '파일 확인', EXTRACT: '음성 추출', TRANSCRIBE: '전사', ALIGN: '시간 정렬', DIARIZE: '화자 분석', ATTRIBUTE: '화자 연결', SUMMARIZE: '회의록 생성', SAVE: '저장' }

export function Library() {
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [selected, setSelected] = useState<Meeting | null>(null)
  const [showUpload, setShowUpload] = useState(false)
  const [media, setMedia] = useState<Media | null>(null)
  const [busy, setBusy] = useState('')
  const [deleting, setDeleting] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  useEffect(() => {
    if (!deleting.length) return
    const timer = setInterval(() => { void Promise.all(deleting.map(async id => {
      const result = await api<{ status: string }>(`/deletions/${id}`)
      if (result.status === 'deleted') { setDeleting(previous => previous.filter(value => value !== id)); setNotice('회의 자료 삭제가 완료됐습니다.') }
    })).catch(e => setError(e.message)) }, 2000)
    return () => clearInterval(timer)
  }, [deleting])
  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const [list, queue] = await Promise.all([api<Meeting[]>(`/meetings?q=${encodeURIComponent(search)}`), api<Job[]>('/jobs')])
        if (!stopped) { setMeetings(list); setJobs(queue) }
      } catch (e) { if (!stopped) setError((e as Error).message) }
      if (!stopped) timer = setTimeout(refresh, 2000)
    }
    void refresh()
    return () => { stopped = true; clearTimeout(timer) }
  }, [search])
  useEffect(() => {
    let stopped = false
    if (selected) api<Media>(`/meetings/${selected.id}/media`).then(value => { if (!stopped) setMedia(value) }).catch(e => setError(e.message))
    return () => { stopped = true }
  }, [selected, jobs])
  async function action(job: Job, verb: string) {
    setBusy(job.id); setError('')
    try { const updated = await api<Job>(`/jobs/${job.id}/${verb}`, { method: 'POST' }); setJobs(previous => previous.map(item => item.id === job.id ? updated : item)) }
    catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  return <>
    <div className="page-title"><div><span className="eyebrow">MEETING LIBRARY</span><h1>내 회의</h1><p>대화의 기록을, 다음 행동으로.</p></div>
      <button className="primary" onClick={() => setShowUpload(true)}>새 회의 업로드</button></div>
    {showUpload && <Upload busy={uploading} setBusy={setUploading} done={() => { setShowUpload(false); setSearch('') }} close={() => setShowUpload(false)} />}
    <div className="toolbar"><label>제목·본문 검색<input type="search" value={search} onChange={e => setSearch(e.target.value)} /></label>
      <label>표시<select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">모든 회의</option><option value="pending">진행·확인 필요</option><option value="complete">완료</option></select></label></div>
    {error && <p role="alert" className="error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <div className="meeting-list">{meetings.filter(meeting => {
      const job = jobs.find(value => value.meeting_id === meeting.id)
      const complete = job?.state.startsWith('COMPLETED')
      return filter === 'all' || (filter === 'complete' ? complete : !complete)
    }).map(meeting => {
      const job = jobs.find(value => value.meeting_id === meeting.id)
      const options = JSON.parse(meeting.settings_json)
      return <article className="meeting-row" key={meeting.id}><div><button className="meeting-title" onClick={() => { if (selected?.id === meeting.id || confirmNavigation()) { setSelected(meeting); setMedia(null) } }}>{meeting.title}</button>
        <p>{options.occurred_at ? new Date(options.occurred_at).toLocaleString('ko-KR') : '회의 일시 미입력'} · {job ? states[job.state] : '파일 업로드 대기'}</p>
        <p>{meeting.duration_ms == null ? '길이 확인 전' : `${Math.floor(meeting.duration_ms / 60000)}분 ${Math.floor(meeting.duration_ms / 1000) % 60}초`} · {meeting.minutes_status === 'confirmed' ? '확정 회의록' : meeting.minutes_status === 'draft' ? '회의록 초안' : '회의록 없음'}</p></div>
        {job && <div className="job-actions"><span>{job.state === 'RUNNING' ? stages[job.stage] : ''}{job.queue_position ? ` · 대기 ${job.queue_position}번` : ''}</span>
          <small>처리 시간 {Math.floor(job.elapsed_seconds || 0)}초</small>
          {(job.blocked_reason || job.error_code) && <p role="status">{job.blocked_reason || job.error_code}</p>}
          {['QUEUED', 'RUNNING', 'BLOCKED', 'INTERRUPTED', 'FAILED'].includes(job.state) && <button disabled={busy === job.id} onClick={() => void action(job, 'cancel')}>취소</button>}
          {['BLOCKED', 'INTERRUPTED', 'FAILED', 'CANCELLED'].includes(job.state) && <button disabled={busy === job.id} onClick={() => void action(job, 'retry')}>재시도</button>}
          {job.transcript_version && ['BLOCKED', 'INTERRUPTED', 'FAILED', 'CANCELLED'].includes(job.state) && <button disabled={busy === job.id} onClick={() => void action(job, 'finish-transcript-only')}>전사만 완료</button>}
        </div>}</article>
    })}</div>
    {!meetings.length && <p className="empty-state">{search ? '검색 결과가 없습니다.' : '첫 음성 또는 영상 파일을 업로드하세요.'}</p>}
    {jobs.some(job => ['BLOCKED', 'INTERRUPTED'].includes(job.state)) && <p role="status">확인이 필요한 앞선 작업이 대기열을 멈추고 있습니다. 해당 작업을 재시도하거나 취소하세요.</p>}
    {selected && <section className="settings-card" aria-label="선택한 회의"><button className="secondary" onClick={() => { if (confirmNavigation()) setSelected(null) }}>닫기</button><h2>{selected.title}</h2>
      <button className="secondary" disabled={busy === selected.id} onClick={async () => {
        if (!window.confirm('이 회의의 원본, 음성, 전사, 회의록과 화자 자료를 모두 삭제할까요? 진행 중인 작업도 취소되며 되돌릴 수 없습니다.')) return
        setBusy(selected.id); setError('')
        try {
          const result = await api<{ status: string }>(`/meetings/${selected.id}`, { method: 'DELETE' })
          setMeetings(previous => previous.filter(value => value.id !== selected.id)); setSelected(null)
          if (result.status === 'deleting') { setDeleting(previous => [...previous, selected.id]); setNotice('작업 종료와 자료 정리를 기다리는 중입니다.') }
          else setNotice('회의 자료 삭제가 완료됐습니다.')
        } catch (e) { setError((e as Error).message) } finally { setBusy('') }
      }}>회의 전체 삭제</button>
      <MeetingDetail key={selected.id} id={selected.id} audioAvailable={media?.audio_available || false} />
      {media?.media?.duration_ms != null && <p>길이 {Math.round(media.media.duration_ms / 1000)}초</p>}
      {jobs.filter(job => job.meeting_id === selected.id && job.blocked_reason === 'AUDIO_TRACK_REQUIRED').map(job => <div key={job.id}><p>처리할 오디오 트랙을 선택하세요.</p>{media?.media?.tracks.map(track => <button key={track.index} disabled={busy === job.id} onClick={() => void action(job, `track?track=${track.index}`)}>트랙 {track.index} · {track.language || '언어 미상'} · {track.codec}</button>)}</div>)}
      {selected.minutes_revision && <div className="toolbar"><a href={`/api/meetings/${selected.id}/export?format=md`}>Markdown 내보내기</a><a href={`/api/meetings/${selected.id}/export?format=txt`}>텍스트 내보내기</a></div>}
    </section>}
  </>
}

function Upload({ busy, setBusy, done, close }: { busy: boolean; setBusy: (value: boolean) => void; done: () => void; close: () => void }) {
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [language, setLanguage] = useState('auto')
  const [speakers, setSpeakers] = useState('')
  const [date, setDate] = useState('')
  const [consent, setConsent] = useState(false)
  const [progress, setProgress] = useState(0)
  const [transferred, setTransferred] = useState(0)
  const [error, setError] = useState('')
  const pending = useRef<{ meeting: string; key: string } | null>(null)
  useUnsaved(busy)
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!file) return
    if (file.size > 2_000_000_000) { setError('파일은 2GB 이하로 선택하세요.'); return }
    setBusy(true); setError('')
    try {
      if (!pending.current) {
        const meeting = await api<Meeting>('/meetings', { method: 'POST', body: JSON.stringify({ title, language, speakers: speakers ? Number(speakers) : null,
          occurred_at: date ? new Date(date).toISOString() : null, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, allow_external_text: consent }) })
        pending.current = { meeting: meeting.id, key: crypto.randomUUID() }
      }
      await uploadFile(pending.current.meeting, file, pending.current.key, (percent, loaded) => { setProgress(percent); setTransferred(loaded) })
      done()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <form className="settings-card upload-form" onSubmit={submit} onDragOver={event => event.preventDefault()} onDrop={event => {
    event.preventDefault()
    if (busy || pending.current) return
    if (event.dataTransfer.files.length !== 1) { setError('파일은 한 번에 하나만 올려주세요.'); return }
    const selectedFile = event.dataTransfer.files[0]; setFile(selectedFile); if (!title) setTitle(selectedFile.name)
  }}><h2>새 회의</h2>
    <fieldset disabled={busy || !!pending.current}><label>회의 제목<input required maxLength={200} value={title} onChange={e => setTitle(e.target.value)} /></label>
      <label>음성 또는 영상 파일<input type="file" required={!file} accept=".wav,.mp3,.m4a,.mp4,.mov,.webm,.mkv,.ogg,.flac" onChange={e => { const value = e.target.files?.[0] || null; setFile(value); if (value && !title) setTitle(value.name) }} /></label>
      <p>파일을 선택하거나 이 영역에 끌어 놓으세요. {file ? `선택: ${file.name}` : ''}</p>
      <p>파일 1개 · 최대 2GB · 최대 120분. 영상에서는 음성만 처리합니다.</p>
      <div className="toolbar"><label>언어<select value={language} onChange={e => setLanguage(e.target.value)}><option value="auto">자동 · 혼용</option><option value="ko">한국어</option><option value="en">영어</option></select></label>
        <label>실제 발화자 수<select value={speakers} onChange={e => setSpeakers(e.target.value)}><option value="">자동</option>{Array.from({ length: 12 }, (_, i) => <option key={i} value={i + 1}>{i + 1}명</option>)}</select></label>
        <label>회의 일시 (현재 시간대)<input type="datetime-local" value={date} onChange={e => setDate(e.target.value)} /></label></div>
      <label className="checkbox"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />회의 텍스트를 Codex로 보내 회의록 생성 허용</label>
      <p>음성은 로컬에서 처리합니다. 허용하지 않으면 전사만 저장합니다. Codex 처리는 구독 사용량을 소비하며 서버 보관 정책이 적용됩니다.</p>
    </fieldset>
    {busy && <div role="status"><progress value={progress} max={100} /> 업로드 {progress}% · {transferred.toLocaleString()} / {file?.size.toLocaleString()} 바이트{progress === 100 ? ' · 서버 저장 확인 중' : ''}</div>}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="toolbar"><button className="primary" disabled={busy || !file}>{busy ? '업로드 중…' : pending.current ? '같은 업로드 재시도' : '업로드하고 처리 시작'}</button><button type="button" className="secondary" disabled={busy} onClick={close}>닫기</button></div>
  </form>
}
