import { sentenceLines } from './prose'
import { confirmAction } from './confirm'
import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Meeting } from './Library'
import { timestamp } from './MeetingDetail'

type Video = { summary: string; revision: number; metadata: { source_url?: string; channel?: string; published_at?: string; generated_at?: string }; topics: { id: string; title: string; text: string; starts: { start_ms: number }[] }[]; claims: { text: string; attribution: string; kind: string }[] }
type Result = { id: string; document: Video }
function sourceLink(source: string | undefined) {
  if (!source) return null
  try {
    const url = new URL(source)
    if (url.origin !== 'https://www.youtube.com' || url.pathname !== '/watch' || !/^[\w-]{11}$/.test(url.searchParams.get('v') || '')) return null
    return `https://www.youtube.com/watch?v=${url.searchParams.get('v')}`
  } catch { return null }
}

export function VideoDetail({ id, audioAvailable, headerActions }: { id: string; audioAvailable: boolean; headerActions?: React.ReactNode }) {
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const audio = useRef<HTMLAudioElement>(null)
  const generationKey = useRef<string | null>(null)
  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const value = await api<Meeting>(`/meetings/${id}`)
        if (stopped) return
        setMeeting(value)
        if (value.minutes_revision) {
          const document = await api<Result>(`/meetings/${id}/minutes`)
          if (!stopped) { setResult(document); setError('') }
        }
      } catch (e) { if (!stopped) setError((e as Error).message) }
      if (!stopped) timer = setTimeout(refresh, 2000)
    }
    void refresh()
    return () => { stopped = true; clearTimeout(timer) }
  }, [id])
  async function regenerate() {
    if (!meeting?.transcript_version || !(await confirmAction('저장된 전사를 사용해 AI 요약을 다시 생성할까요?'))) return
    setBusy(true); setError('')
    generationKey.current ||= crypto.randomUUID()
    try {
      await api(`/meetings/${id}/minutes/generate`, { method: 'POST', headers: { 'Idempotency-Key': generationKey.current }, body: JSON.stringify({ expected_revision: meeting.revision, transcript_version: meeting.transcript_version, allow_external_text: true, new_draft: !!meeting.minutes_revision }) })
      generationKey.current = null; setNotice('AI 요약 작업을 등록했습니다. 완료되면 새 요약을 표시합니다.')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const document = result?.document
  const original = sourceLink(document?.metadata.source_url)
  return <div className="detail-workspace video-detail">
    <div className="toolbar">{original && <a className="generation-action" href={original} target="_blank" rel="noopener noreferrer">원본 영상</a>}{headerActions}</div>
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    <section aria-label="영상 요약 내용">
      <div className="minutes-heading"><h3>영상 요약</h3></div>
      {document && <div className="video-metadata"><span>채널 {document.metadata.channel || '미상'}</span><span>게시일 {document.metadata.published_at || '미상'}</span><span>분석 시각 {document.metadata.generated_at ? new Date(document.metadata.generated_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '미상'}</span></div>}
      {audioAvailable ? <audio ref={audio} controls preload="metadata" aria-label="영상 음성" src={`/api/meetings/${id}/audio`} /> : <p>재생할 로컬 음성이 없습니다. 저장된 요약본은 계속 열람할 수 있습니다.</p>}
      {!document ? <p>아직 생성된 요약본이 없습니다.</p> : <>
        <h4>핵심 요약</h4><p className="document-prose">{sentenceLines(document.summary)}</p>
        <h4>주요 주제</h4>
        <div className="topic-list">{document.topics.map(topic => <article className="topic-card" key={topic.id}>
          <h5>{topic.title}</h5><p className="document-prose">{sentenceLines(topic.text)}</p>
          <div className="toolbar">{topic.starts.map((start, index) => audioAvailable ? <button key={index} onClick={() => {
            if (audio.current) { audio.current.currentTime = start.start_ms / 1000; void audio.current.play().catch(() => setError('재생 버튼으로 음성을 시작하세요.')) }
          }}>{timestamp(start.start_ms)}</button> : <span key={index}>{timestamp(start.start_ms)} · 음성 없음</span>)}</div>
        </article>)}</div>
      </>}
    </section>
    <div className="generation-box"><h3>AI를 사용한 영상 요약 생성</h3><div className="toolbar">
      <button className="generation-action" disabled={busy || !meeting?.transcript_version} onClick={() => void regenerate()}>{meeting?.minutes_revision ? '요약 재생성' : '요약 생성'}</button>
      {result && <><a className="generation-action" href={`/api/meetings/${id}/export?format=txt&version=${encodeURIComponent(result.id)}`}>텍스트 내보내기</a><a className="generation-action" href={`/api/meetings/${id}/export?format=md&version=${encodeURIComponent(result.id)}`}>Markdown 내보내기</a></>}
    </div></div>
  </div>
}
