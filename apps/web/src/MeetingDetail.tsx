import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Meeting } from './Library'
import { confirmNavigation, useUnsaved } from './unsaved'

type Segment = { id: string; text: string; speaker_id: string | null; start_ms: number; end_ms: number; needs_review: boolean; overlap: boolean }
type Transcript = { id: string; meeting_revision: number; parent_id: string | null; content: { speakers: Record<string, { name: string }>; segments: Segment[]; merge_candidates?: { left: string; right: string; score?: number; similarity?: number }[] } }
type Item = { id: string; text?: string; task?: string; source_segment_ids: string[]; review_status: 'needs_review' | 'verified' | 'user_authored'; owner_speaker_id?: string | null; due_date?: string | null; due_date_original_expression?: string | null }
type Content = { schema_version: 1; meeting_id: string; transcript_version: string; revision: number; language: 'ko'; status: 'draft' | 'confirmed'; summary: string; topics: Item[]; decisions: Item[]; action_items: Item[]; open_questions: Item[]; review_notes: string[] }
type Minutes = { id: string; meeting_revision: number; stale_transcript: boolean; content: Content }
type Version = { id: string; kind?: string; status?: string; revision?: number; created_at: number }
type Section = 'topics' | 'decisions' | 'action_items' | 'open_questions'
const sections: [Section, string][] = [['topics', '주요 논의'], ['decisions', '결정'], ['action_items', '할 일'], ['open_questions', '미결 질문']]
const timestamp = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`

export function MeetingDetail({ id, audioAvailable }: { id: string; audioAvailable: boolean }) {
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [minutes, setMinutes] = useState<Minutes | null>(null)
  const [minutesTranscript, setMinutesTranscript] = useState<Transcript | null>(null)
  const [draft, setDraft] = useState<Content | null>(null)
  const [dirty, setDirty] = useState(false)
  useUnsaved(dirty)
  const [transcriptVersions, setTranscriptVersions] = useState<Version[]>([])
  const [minutesVersions, setMinutesVersions] = useState<Version[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [page, setPage] = useState(0)
  const [focused, setFocused] = useState('')
  const [query, setQuery] = useState('')
  const [consent, setConsent] = useState(false)
  const [source, setSource] = useState('')
  const [target, setTarget] = useState('')
  const audio = useRef<HTMLAudioElement>(null)
  const generationKey = useRef<string | null>(null)
  const base = `/meetings/${id}`
  async function reload() {
    const current = await api<Meeting>(base)
    const [text, notes, textVersions, noteVersions] = await Promise.all([
      current.transcript_version ? api<Transcript>(`${base}/transcript`) : null,
      current.minutes_revision ? api<Minutes>(`${base}/minutes`) : null,
      api<Version[]>(`${base}/transcript/versions`), api<Version[]>(`${base}/minutes/versions`),
    ])
    setMeeting(current); setTranscript(text); setMinutes(notes); setDraft(notes?.content || null)
    setMinutesTranscript(notes ? (text?.id === notes.content.transcript_version ? text : await api<Transcript>(`${base}/transcript?version=${encodeURIComponent(notes.content.transcript_version)}`)) : null)
    setTranscriptVersions(textVersions); setMinutesVersions(noteVersions); setDirty(false); setPage(0); setQuery('')
  }
  useEffect(() => { void reload().catch(e => setError(e.message)) }, [id]) // keyed by meeting in the parent
  async function perform(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await work() } catch (e) {
      const message = (e as Error).message
      setError(message.includes('REVISION_CONFLICT') || message.includes('TRANSCRIPT_CHANGED')
        ? '다른 탭 또는 작업에서 내용이 바뀌었습니다. 입력은 유지했습니다. 필요한 내용을 복사한 뒤 최신 내용 불러오기를 눌러주세요.' : message)
    } finally { setBusy(false) }
  }
  const editableTranscript = !!transcript && transcript.id === meeting?.transcript_version && !dirty
  const editableMinutes = !!minutes && minutes.id === meeting?.minutes_revision && minutes.content.transcript_version === meeting?.transcript_version
  async function edit(operation: Record<string, unknown>) {
    await perform(async () => {
      await api(`${base}/transcript/edits`, { method: 'POST', body: JSON.stringify({ expected_revision: meeting!.revision, operation }) })
      await reload(); setNotice('새 전사 버전을 저장했습니다. 원본은 보존됩니다.')
    })
  }
  function change(section: Section, index: number, item: Item) {
    if (!draft) return
    setDraft({ ...draft, [section]: draft[section].map((value, i) => i === index ? item : value) }); setDirty(true)
  }
  async function selectMinutes(version: string) {
    if (!confirmNavigation()) return
    await perform(async () => {
      const value = await api<Minutes>(`${base}/minutes?version=${encodeURIComponent(version)}`)
      const text = await api<Transcript>(`${base}/transcript?version=${encodeURIComponent(value.content.transcript_version)}`)
      setMinutes(value); setDraft(value.content); setTranscript(text); setMinutesTranscript(text); setDirty(false); setPage(0); setQuery('')
    })
  }
  function jump(segment: Segment, snapshot = transcript) {
    if (snapshot) setTranscript(snapshot)
    setQuery(''); setPage(Math.floor(Math.max(0, snapshot?.content.segments.findIndex(item => item.id === segment.id) ?? 0) / 50)); setFocused(segment.id)
    if (audio.current) { audio.current.currentTime = segment.start_ms / 1000; audio.current.focus() }
    setTimeout(() => document.getElementById(`segment-${segment.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 0)
  }
  const visible = transcript?.content.segments.filter(segment => !query || segment.text.includes(query)) || []
  return <div className="detail-workspace">
    <div className="toolbar"><button disabled={busy} className="secondary" onClick={() => {
      if (confirmNavigation()) void perform(reload)
    }}>최신 내용 불러오기</button>{notice && <span role="status">{notice}</span>}</div>
    {error && <p role="alert" className="error">{error}</p>}
    {audioAvailable ? <audio ref={audio} controls preload="metadata" src={`/api${base}/audio`} aria-label="회의 음성" /> : <p>재생 가능한 음성이 아직 없거나 보관 기간이 끝났습니다. 근거 텍스트는 아래에서 확인할 수 있습니다.</p>}
    <section aria-label="전사문"><h3>전사문</h3>
      {transcript ? <>
        <div className="toolbar"><label>전사 버전<select aria-label="전사 버전" disabled={busy || dirty} value={transcript.id} onChange={e => void perform(async () => {
          if (confirmNavigation()) { setTranscript(await api<Transcript>(`${base}/transcript?version=${encodeURIComponent(e.target.value)}`)); setPage(0); setQuery('') }
        })}>{transcriptVersions.map((version, i) => <option key={version.id} value={version.id}>{version.kind === 'original' ? '원본' : '편집본'} · {transcriptVersions.length - i}</option>)}</select></label>
          <button disabled={busy || !editableTranscript || !transcript.parent_id} onClick={() => void edit({ type: 'undo' })}>이전 수정 되돌리기</button>
          <label>발언 검색<input value={query} onChange={e => { setQuery(e.target.value); setPage(0) }} /></label></div>
        {!editableTranscript && <p>과거 버전이거나 회의록에 저장하지 않은 변경이 있어 전사 편집을 잠시 제한합니다.</p>}
        <div className="speaker-list">{Object.entries(transcript.content.speakers).map(([speaker, value]) => <SpeakerName key={`${transcript.id}-${speaker}`} name={value.name} disabled={busy || !editableTranscript} save={name => edit({ type: 'rename', speaker_id: speaker, name })} />)}</div>
        <details><summary>화자 병합 · 후보 확인</summary><p>같은 사람임을 직접 확인한 뒤 병합하세요. 되돌리기로 이전 버전을 복원할 수 있습니다.</p>
          {(transcript.content.merge_candidates || []).map(candidate => <p key={`${candidate.left}-${candidate.right}`}>후보: {transcript.content.speakers[candidate.left]?.name} ↔ {transcript.content.speakers[candidate.right]?.name}</p>)}
          <div className="toolbar"><label>합칠 화자<select aria-label="합칠 화자" value={source} onChange={e => setSource(e.target.value)}><option value="">선택</option>{Object.entries(transcript.content.speakers).map(([key, value]) => <option key={key} value={key}>{value.name}</option>)}</select></label>
            <label>남길 화자<select aria-label="남길 화자" value={target} onChange={e => setTarget(e.target.value)}><option value="">선택</option>{Object.entries(transcript.content.speakers).map(([key, value]) => <option key={key} value={key}>{value.name}</option>)}</select></label>
            <button disabled={busy || !editableTranscript || !source || !target || source === target} onClick={() => {
              if (window.confirm('선택한 두 화자의 발언을 하나로 합칠까요?')) void edit({ type: 'merge', source_speaker_id: source, speaker_id: target })
            }}>화자 병합</button></div></details>
        <div className="transcript-list">{visible.slice(page * 50, (page + 1) * 50).map(segment => <SegmentEditor key={`${transcript.id}-${segment.id}`} segment={segment} speakers={transcript.content.speakers} disabled={busy || !editableTranscript} focused={focused === segment.id} jump={() => jump(segment)} edit={edit} />)}</div>
        <div className="toolbar"><button disabled={page === 0} onClick={() => setPage(page - 1)}>이전 발언</button><span>{page + 1} / {Math.max(1, Math.ceil(visible.length / 50))}</span><button disabled={(page + 1) * 50 >= visible.length} onClick={() => setPage(page + 1)}>다음 발언</button></div>
      </> : <p>전사가 완료되면 발언을 확인하고 수정할 수 있습니다.</p>}
    </section>
    <section aria-label="회의록"><h3>회의록</h3>
      <div className="generation-box"><label className="checkbox"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />현재 전사 텍스트의 Codex 전송 허용</label>
        <p>구독 사용량을 소비합니다. 새 초안을 만들어도 기존 확정본은 보존됩니다.</p>
        <button disabled={busy || dirty || !consent || !meeting?.transcript_version} onClick={() => void perform(async () => {
          if (meeting!.minutes_revision && !window.confirm('현재 전사로 새 회의록 초안을 생성할까요?')) return
          generationKey.current ||= crypto.randomUUID()
          await api(`${base}/minutes/generate`, { method: 'POST', headers: { 'Idempotency-Key': generationKey.current }, body: JSON.stringify({ expected_revision: meeting!.revision, transcript_version: meeting!.transcript_version, allow_external_text: true, new_draft: !!meeting!.minutes_revision }) })
          generationKey.current = null; await reload(); setNotice('생성 작업을 대기열에 등록했습니다. 완료 후 최신 내용을 불러오세요.')
        })}>{meeting?.minutes_revision ? '새 초안 생성' : '회의록 생성'}</button></div>
      {minutes && draft ? <>
        <div className="toolbar"><label>회의록 버전<select aria-label="회의록 버전" disabled={busy} value={minutes.id} onChange={e => void selectMinutes(e.target.value)}>{minutesVersions.map(version => <option key={version.id} value={version.id}>{version.revision} · {version.status === 'confirmed' ? '확정' : '초안'}</option>)}</select></label>
          <a href={`/api${base}/export?format=md&version=${minutes.id}`}>이 버전 Markdown</a><a href={`/api${base}/export?format=txt&version=${minutes.id}`}>이 버전 텍스트</a></div>
        {minutes.stale_transcript && <p role="status">이 회의록은 이전 전사를 바탕으로 작성됐습니다. 근거는 당시 전사에서 확인합니다. 현재 전사로 새 초안을 생성할 수 있습니다.</p>}
        <fieldset disabled={busy || !editableMinutes}><legend>{draft.status === 'confirmed' ? '확정본 · 수정 저장 시 새 초안으로 보존' : '초안 편집'}</legend>
          <label>요약<textarea aria-label="요약" rows={5} maxLength={20000} value={draft.summary} onChange={e => { setDraft({ ...draft, summary: e.target.value }); setDirty(true) }} /></label>
          {sections.map(([section, label]) => <div key={section}><h4>{label}</h4>{draft[section].map((item, index) => <div className="minutes-item" key={item.id}>
            <label>{label} 내용<textarea maxLength={10000} value={item.text ?? item.task ?? ''} onChange={e => change(section, index, { ...item, [section === 'action_items' ? 'task' : 'text']: e.target.value })} /></label>
            {section === 'action_items' && <div className="toolbar"><label>담당자<select aria-label="담당자" value={item.owner_speaker_id || ''} onChange={e => change(section, index, { ...item, owner_speaker_id: e.target.value || null })}><option value="">미정</option>{Object.entries(minutesTranscript?.content.speakers || {}).map(([key, value]) => <option key={key} value={key}>{value.name}</option>)}</select></label>
              <label>기한<input type="date" value={item.due_date || ''} onChange={e => change(section, index, { ...item, due_date: e.target.value || null })} /></label>
              <label>원래 날짜 표현<input maxLength={200} value={item.due_date_original_expression || ''} onChange={e => change(section, index, { ...item, due_date_original_expression: e.target.value || null })} /></label></div>}
            <label>검토 상태<select aria-label="검토 상태" value={item.review_status} onChange={e => {
              const review = e.target.value as Item['review_status']
              if (review === 'user_authored' && !window.confirm('사용자 작성으로 바꾸면 발언 근거 연결을 해제합니다. 계속할까요?')) return
              change(section, index, { ...item, review_status: review, source_segment_ids: review === 'user_authored' ? [] : item.source_segment_ids })
            }}><option value="needs_review">검토 필요</option><option value="verified">근거 확인함</option><option value="user_authored">사용자 작성 · 발언 근거 없음</option></select></label>
            <Evidence item={item} transcript={minutesTranscript} jump={segment => jump(segment, minutesTranscript)} update={updated => change(section, index, updated)} />
            <button type="button" onClick={() => { setDraft({ ...draft, [section]: draft[section].filter((_, i) => i !== index) }); setDirty(true) }}>항목 삭제</button>
          </div>)}<button type="button" disabled={draft[section].length >= 200} onClick={() => {
            const common = { id: crypto.randomUUID(), source_segment_ids: [], review_status: 'user_authored' as const }
            const item = section === 'action_items' ? { ...common, task: '', owner_speaker_id: null, due_date: null, due_date_original_expression: null } : { ...common, text: '' }
            setDraft({ ...draft, [section]: [...draft[section], item] }); setDirty(true)
          }}>{label} 직접 추가</button></div>)}
          <label>검토 메모 (한 줄에 하나)<textarea value={draft.review_notes.join('\n')} onChange={e => { setDraft({ ...draft, review_notes: e.target.value.split('\n').filter(Boolean) }); setDirty(true) }} /></label>
        </fieldset>
        <details className="readonly-evidence"><summary>저장된 회의록의 근거 보기</summary>{sections.map(([section, label]) => <div key={section}><h4>{label}</h4>{minutes.content[section].map(item => <div key={item.id}><p>{item.text ?? item.task}</p>{item.source_segment_ids.map(sourceId => {
          const segment = minutesTranscript?.content.segments.find(value => value.id === sourceId)
          return segment ? <button key={sourceId} onClick={() => jump(segment, minutesTranscript)}>{timestamp(segment.start_ms)} · {segment.text}</button> : null
        })}</div>)}</div>)}</details>
        <div className="toolbar"><button className="primary" disabled={busy || !dirty || !editableMinutes} onClick={() => void perform(async () => {
          await api(`${base}/minutes/${minutes.id}`, { method: 'PATCH', body: JSON.stringify({ expected_revision: meeting!.revision, content: draft }) }); await reload(); setNotice('새 회의록 초안을 저장했습니다.')
        })}>수정본 저장</button>
          <button disabled={busy || dirty || !editableMinutes || draft.status === 'confirmed' || [...draft.decisions, ...draft.action_items].some(item => item.review_status === 'needs_review')} onClick={() => void perform(async () => {
            await api(`${base}/minutes/${minutes.id}/confirm`, { method: 'POST', body: JSON.stringify({ expected_revision: meeting!.revision }) }); await reload(); setNotice('확정 버전을 저장했습니다.')
          })}>검토한 회의록 확정</button></div>
        {dirty && <p role="status">저장하지 않은 변경이 있습니다. 확정하려면 먼저 저장하세요.</p>}
        {!editableMinutes && <p>과거 회의록은 읽기 전용입니다. 최신 버전을 불러오거나 현재 전사로 새 초안을 생성하세요.</p>}
      </> : <p>회의록이 아직 없습니다. 전사가 준비되면 텍스트 전송을 허용하고 생성할 수 있습니다.</p>}
    </section>
  </div>
}

function SpeakerName({ name, disabled, save }: { name: string; disabled: boolean; save: (name: string) => Promise<void> }) {
  const [value, setValue] = useState(name)
  useUnsaved(value !== name)
  return <form className="toolbar" onSubmit={e => { e.preventDefault(); void save(value) }}><label>화자 이름<input maxLength={80} required disabled={disabled} value={value} onChange={e => setValue(e.target.value)} /></label><button disabled={disabled || !value.trim() || value === name}>이름 저장</button></form>
}

function SegmentEditor({ segment, speakers, disabled, focused, jump, edit }: { segment: Segment; speakers: Transcript['content']['speakers']; disabled: boolean; focused: boolean; jump: () => void; edit: (operation: Record<string, unknown>) => Promise<void> }) {
  const [value, setValue] = useState(segment.text)
  useUnsaved(value !== segment.text)
  return <article id={`segment-${segment.id}`} className={`segment ${focused ? 'focused' : ''}`}><div className="toolbar"><button onClick={jump}>{timestamp(segment.start_ms)} 근거 이동</button><span>{segment.needs_review ? '검토 필요' : ''} {segment.overlap ? '겹친 발언' : ''}</span>
    <label>발언 화자<select aria-label="발언 화자" disabled={disabled} value={segment.speaker_id || ''} onChange={e => void edit({ type: 'reassign', segment_ids: [segment.id], speaker_id: e.target.value || null })}><option value="">미확정</option>{Object.entries(speakers).map(([key, speaker]) => <option key={key} value={key}>{speaker.name}</option>)}</select></label></div>
    <label>발언 내용<textarea aria-label="발언 내용" disabled={disabled} maxLength={20000} value={value} onChange={e => setValue(e.target.value)} /></label><button disabled={disabled || value === segment.text} onClick={() => void edit({ type: 'text', segment_id: segment.id, text: value })}>발언 저장</button></article>
}

function Evidence({ item, transcript, jump, update }: { item: Item; transcript: Transcript | null; jump: (segment: Segment) => void; update: (item: Item) => void }) {
  const [query, setQuery] = useState('')
  return <div className="evidence"><p>발언 근거</p>{item.source_segment_ids.map(id => {
    const segment = transcript?.content.segments.find(value => value.id === id)
    return <div key={id}>{segment ? <button type="button" onClick={() => jump(segment)}>{timestamp(segment.start_ms)} · {segment.text}</button> : <span>해당 회의록의 전사 버전을 선택하면 근거를 볼 수 있습니다.</span>}<button type="button" onClick={() => update({ ...item, source_segment_ids: item.source_segment_ids.filter(value => value !== id) })}>근거 제거</button></div>
  })}{item.review_status !== 'user_authored' && <><label>근거 발언 검색<input value={query} onChange={e => setQuery(e.target.value)} /></label><label>근거 추가<select aria-label="근거 추가" value="" disabled={item.source_segment_ids.length >= 100} onChange={e => { if (e.target.value) update({ ...item, source_segment_ids: [...item.source_segment_ids, e.target.value] }) }}><option value="">발언 선택</option>{transcript?.content.segments.filter(segment => segment.text.trim() && segment.text.includes(query) && !item.source_segment_ids.includes(segment.id)).slice(0, 100).map(segment => <option key={segment.id} value={segment.id}>{timestamp(segment.start_ms)} · {segment.text.slice(0, 100)}</option>)}</select></label><small>검색 결과 중 최대 100개를 표시합니다.</small></>}</div>
}
