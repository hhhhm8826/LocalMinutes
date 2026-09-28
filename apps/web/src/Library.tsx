import { confirmAction } from './confirm'
import { BackButton } from './BackButton'
import { useEffect, useState } from 'react'
import { api } from './api'
import { MeetingDetail } from './MeetingDetail'
import { confirmNavigation } from './unsaved'
import { VideoDetail } from './VideoDetail'
import { Upload } from './Upload'
import { YoutubeInput } from './YoutubeInput'
import { jobError } from './job-error'

export type Meeting = { id: string; title: string; created_at: number; settings_json: string; revision: number; transcript_version: string | null; minutes_revision: string | null; duration_ms?: number | null; minutes_number?: number | null; minutes_status?: string | null }
type Job = { kind: string; source_title: string; source_kind: string; document_kind: string; id: string; meeting_id: string; state: string; stage: string; queue_position: number | null; elapsed_seconds: number; blocked_reason: string | null; error_code: string | null; transcript_version: string | null }
type Media = { audio_available: boolean; media: { duration_ms: number | null; tracks: { index: number; language: string | null; codec: string }[] } | null }
const states: Record<string, string> = { QUEUED: '대기', RUNNING: '처리 중', BLOCKED: '확인 필요', INTERRUPTED: '중단됨', FAILED: '실패', CANCEL_REQUESTED: '취소 중', CANCELLED: '취소됨', COMPLETED: '완료', COMPLETED_TRANSCRIPT_ONLY: '전사 완료' }
const stages: Record<string, string> = { SOURCE_CHECK: '영상 정보 확인', DOWNLOAD: '음성 다운로드', VALIDATE: '파일 확인', EXTRACT: '음성 추출', TRANSCRIBE: '전사', ALIGN: '시간 정렬', DIARIZE: '화자 분석', ATTRIBUTE: '화자 연결', SUMMARIZE: '회의록 생성', SAVE: '저장' }

export function Library({ kind = 'meeting' }: { kind?: 'meeting' | 'video_summary' }) {
  const video = kind === 'video_summary'
  const uploadHash = video ? '#new-video' : '#new-meeting'
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [selected, setSelected] = useState<Meeting | null>(null)
  const [showUpload, setShowUpload] = useState(location.hash === uploadHash)
  const [showYoutube, setShowYoutube] = useState(video && location.hash === '#new-youtube')
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
        const [list, queue] = await Promise.all([api<Meeting[]>(`/meetings?document_kind=${kind}&q=${encodeURIComponent(search)}`), api<Job[]>('/jobs')])
        if (!stopped) { setMeetings(list); setJobs(queue) }
      } catch (e) { if (!stopped) setError((e as Error).message) }
      if (!stopped) timer = setTimeout(refresh, 2000)
    }
    void refresh()
    return () => { stopped = true; clearTimeout(timer) }
  }, [search, kind])
  useEffect(() => {
    let stopped = false
    if (selected) api<Media>(`/meetings/${selected.id}/media`).then(value => { if (!stopped) setMedia(value) }).catch(e => setError(e.message))
    return () => { stopped = true }
  }, [selected, jobs])
  useEffect(() => {
    const navigate = async () => {
      if (await confirmNavigation()) {
        setShowUpload(location.hash === uploadHash)
        setShowYoutube(video && location.hash === '#new-youtube')
      }
      else history.pushState({}, '', uploadHash)
    }
    window.addEventListener('popstate', navigate)
    return () => window.removeEventListener('popstate', navigate)
  }, [])
  async function closeUpload() {
    if (!(await confirmNavigation())) return
    history.replaceState({}, '', location.pathname + location.search)
    setShowUpload(false); setShowYoutube(false)
  }
  async function action(job: Job, verb: string) {
    if (verb === 'cancel' && !(await confirmAction('진행 중이거나 대기 중인 분석을 취소할까요?'))) return
    setBusy(job.id); setError('')
    try { const updated = await api<Job>(`/jobs/${job.id}/${verb}`, { method: 'POST' }); setJobs(previous => previous.map(item => item.id === job.id ? updated : item)) }
    catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  const detailActions = selected ? <button className="secondary" disabled={busy === selected.id} onClick={async () => {
        if (!(await confirmAction('이 회의의 원본, 음성, 전사, 회의록과 화자 자료를 모두 삭제할까요? 진행 중인 작업도 취소되며 되돌릴 수 없습니다.'))) return
        setBusy(selected.id); setError('')
        try {
          const result = await api<{ status: string }>(`/meetings/${selected.id}`, { method: 'DELETE' })
          setMeetings(previous => previous.filter(value => value.id !== selected.id)); setSelected(null)
          if (result.status === 'deleting') { setDeleting(previous => [...previous, selected.id]); setNotice('작업 종료와 자료 정리를 기다리는 중입니다.') }
          else setNotice('회의 자료 삭제가 완료됐습니다.')
        } catch (e) { setError((e as Error).message) } finally { setBusy('') }
      }}>{video ? '영상 자료 전체 삭제' : '회의 전체 삭제'}</button> : null
  function inputDone(meeting: Meeting) {
    history.replaceState({}, '', location.pathname + location.search)
    setShowUpload(false); setShowYoutube(false); setSelected(meeting); setMedia(null); setNotice('입력을 등록했습니다. 선택한 자료의 처리 상태를 확인하세요.')
  }
  if (showYoutube) return <YoutubeInput done={inputDone} close={closeUpload} />
  const analysisJobs = jobs.filter(job => job.kind !== 'summarize')
  const processing = analysisJobs.some(job => ['RUNNING', 'CANCEL_REQUESTED'].includes(job.state))
  const waiting = analysisJobs.filter(job => job.state === 'QUEUED').length
  const blocked = analysisJobs.some(job => ['BLOCKED', 'INTERRUPTED'].includes(job.state))
  if (showUpload) return <Upload kind={kind} busy={uploading} setBusy={setUploading} close={closeUpload} done={inputDone} />
  return <>
    <div className="page-title"><div><span className="eyebrow">{video ? 'VIDEO LIBRARY' : 'MEETING LIBRARY'}</span><h1>{video ? '영상 요약' : '회의록'}</h1></div>
      <div className="library-actions">
        {video && <button className="primary" onClick={async () => { if (await confirmNavigation()) { history.pushState({}, '', '#new-youtube'); setShowYoutube(true) } }}>YouTube 영상 요약</button>}
        <button className="primary" onClick={async () => { if (await confirmNavigation()) { history.pushState({}, '', uploadHash); setShowUpload(true) } }}>{video ? '새 영상 요약' : '새 회의 업로드'}</button>
      </div></div>
    <div className="toolbar library-filters"><label>제목·본문 검색<input type="search" value={search} onChange={e => setSearch(e.target.value)} /></label>
      <label>표시<select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">{video ? '모든 영상' : '모든 회의'}</option><option value="pending">진행·확인 필요</option><option value="complete">완료</option></select></label><div className="queue-status" role="status">{processing && <span className="queue-spinner" aria-hidden="true" />}전체 대기열 : {processing ? '처리 중' : blocked ? '확인 필요' : '처리 없음'} 및 {waiting}건 대기중</div></div>
    {error && <p role="alert" className="error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <div className="meeting-list">{meetings.filter(meeting => {
      const job = jobs.find(value => value.meeting_id === meeting.id)
      const complete = job?.state.startsWith('COMPLETED')
      return filter === 'all' || (filter === 'complete' ? complete : !complete)
    }).map(meeting => {
      const job = jobs.find(value => value.meeting_id === meeting.id)
      const options = JSON.parse(meeting.settings_json)
      return <article className={`meeting-row${job && ['RUNNING', 'CANCEL_REQUESTED'].includes(job.state) ? ' processing' : ''}`} key={meeting.id}><div><button className="meeting-title" onClick={async () => { if (selected?.id === meeting.id || await confirmNavigation()) { setSelected(meeting); setMedia(null) } }}>{meeting.title}</button>
        <p>{options.occurred_at ? new Date(options.occurred_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : video ? '영상 자료' : '회의 일시 미입력'} · {job ? states[job.state] : '파일 업로드 대기'}</p>
        <p>{meeting.duration_ms == null ? '길이 확인 전' : `${Math.floor(meeting.duration_ms / 60000)}분 ${Math.floor(meeting.duration_ms / 1000) % 60}초`} · {video ? (meeting.minutes_revision ? '요약본' : '요약본 없음') : meeting.minutes_revision ? (meeting.minutes_number === 1 ? '회의록 초안' : `회의록 버전${String(meeting.minutes_number || '').padStart(2, '0')}`) : '회의록 없음'}</p></div>
        {job && <div className="job-actions"><span>{job.state === 'RUNNING' ? (job.document_kind === 'video_summary' && job.stage === 'SUMMARIZE' ? '요약본 생성' : stages[job.stage]) : ''}{job.queue_position ? ` · ${job.kind === 'summarize' ? 'AI 요약 ' : ''}대기 ${job.queue_position}번` : ''}</span>
          {['RUNNING', 'CANCEL_REQUESTED'].includes(job.state) && <small>처리 시간 {Math.floor(job.elapsed_seconds || 0)}초</small>}
          {(job.blocked_reason || job.error_code) && <p role="status">{jobError((job.blocked_reason || job.error_code)!, job.source_kind === 'youtube')}</p>}
          {['QUEUED', 'RUNNING', 'BLOCKED', 'INTERRUPTED', 'FAILED'].includes(job.state) && <button disabled={busy === job.id} onClick={() => void action(job, 'cancel')}>취소</button>}
          {!['AI_WEEKLY_BUDGET_EXHAUSTED', 'AI_DAILY_REQUEST_BUDGET_EXHAUSTED', 'AI_REQUEST_RATE_WAIT'].includes(job.blocked_reason || '') && ['BLOCKED', 'INTERRUPTED', 'FAILED', 'CANCELLED'].includes(job.state) && <button disabled={busy === job.id} onClick={() => void action(job, 'retry')}>재시도</button>}
          {job.transcript_version && ['BLOCKED', 'INTERRUPTED', 'FAILED', 'CANCELLED'].includes(job.state) && <button disabled={busy === job.id} onClick={() => void action(job, 'finish-transcript-only')}>전사만 완료</button>}
        </div>}</article>
    })}</div>
    {!meetings.length && <p className="empty-state">{search ? '검색 결과가 없습니다.' : '첫 음성 또는 영상 파일을 업로드하세요.'}</p>}
    {analysisJobs.some(job => ['BLOCKED', 'INTERRUPTED'].includes(job.state)) && <p role="status">확인이 필요한 앞선 작업이 대기열을 멈추고 있습니다. 해당 작업을 재시도하거나 취소하세요.</p>}
    {selected && <section className="settings-card" aria-label="선택한 회의"><div className="panel-heading"><h2>{meetings.find(m => m.id === selected.id)?.title || selected.title}</h2><BackButton onClick={async () => { if (await confirmNavigation()) setSelected(null) }} /></div>

      <>{video ? <VideoDetail headerActions={detailActions} key={selected.id} id={selected.id} audioAvailable={media?.audio_available || false} /> : <MeetingDetail currentVersion={meetings.find(m => m.id === selected.id)?.minutes_revision} headerActions={detailActions} key={selected.id} id={selected.id} audioAvailable={media?.audio_available || false} />}</>
      {media?.media?.duration_ms != null && <p>길이 {Math.round(media.media.duration_ms / 1000)}초</p>}
      {jobs.filter(job => job.meeting_id === selected.id && job.blocked_reason === 'AUDIO_TRACK_REQUIRED').map(job => <div key={job.id}><p>처리할 오디오 트랙을 선택하세요.</p>{media?.media?.tracks.map(track => <button key={track.index} disabled={busy === job.id} onClick={() => void action(job, `track?track=${track.index}`)}>트랙 {track.index} · {track.language || '언어 미상'} · {track.codec}</button>)}</div>)}
    </section>}
  </>
}
