import { sentenceLines } from './prose'
import { confirmAction } from './confirm'
import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Meeting } from './Library'
import { confirmNavigation, useUnsaved } from './unsaved'

type Item = { id: string; text?: string; task?: string; source_segment_ids: string[]; review_status: string; owner_speaker_id?: string | null; owner_name?: string | null; due_date?: string | null; due_date_original_expression?: string | null }
type Topic = { id: string; title: string; text: string; conclusion: string; conclusion_kind: string; starts: { start_ms: number; end_ms: number; utterance_ids: string[] }[]; source_segment_ids: string[]; origin: string }
type Content = { schema_version: 2; document_kind: 'meeting'; meeting_id: string; transcript_version: string | null; revision: number; language: 'ko'; status: 'draft' | 'confirmed'; user_edited: boolean; metadata: { title: string; occurred_at: string | null }; summary: string; topics: Topic[]; decisions: Item[]; action_items: Item[]; open_questions: Item[]; review_notes: string[] }
type Minutes = { id: string; meeting_revision: number; stale_transcript: boolean; source_available: boolean; document: Content }
type Version = { id: string; kind?: string; status?: string; revision?: number; created_at: number }
function versionLabel(version: Version) {
  if (version.revision === 1) return '초안'
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit' }).formatToParts(new Date(version.created_at * 1000))
  const date = ['month', 'day'].map(type => parts.find(part => part.type === type)!.value).join('')
  return `버전${String(version.revision).padStart(2, '0')}_${date}`
}

type Section = 'decisions' | 'action_items' | 'open_questions'
const sections: [Section, string][] = [['decisions', '결정'], ['action_items', '할 일'], ['open_questions', '미결 사항']]
export function timestamp(ms: number) {
  const seconds = Math.floor(ms / 1000)
  const tail = `${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
  return seconds >= 3600 ? `${String(Math.floor(seconds / 3600)).padStart(2, '0')}:${tail}` : tail
}

export function MeetingDetail({ id, audioAvailable, headerActions, currentVersion }: { id: string; audioAvailable: boolean; headerActions?: React.ReactNode; currentVersion?: string | null }) {
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [minutes, setMinutes] = useState<Minutes | null>(null)
  const [draft, setDraft] = useState<Content | null>(null)
  const [editing, setEditing] = useState(false)
  const [dirty, setDirty] = useState(false)
  useUnsaved(dirty)
  const [minutesVersions, setMinutesVersions] = useState<Version[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const audio = useRef<HTMLAudioElement>(null)
  const generationKey = useRef<string | null>(null)
  const loadSequence = useRef(0)
  const base = `/meetings/${id}`

  async function reload() {
    const sequence = ++loadSequence.current
    const current = await api<Meeting>(base)
    if (sequence !== loadSequence.current) return
    setMeeting(current)
    const [notes, noteVersions] = await Promise.allSettled([
      current.minutes_revision ? api<Minutes>(`${base}/minutes`) : Promise.resolve(null),
      api<Version[]>(`${base}/minutes/versions`),
    ])
    if (sequence !== loadSequence.current) return
    if (notes.status === 'fulfilled') { setMinutes(notes.value); setDraft(notes.value?.document || null); setDirty(false); setEditing(false) }
    else setError(`결과 불러오기: ${notes.reason.message}`)
    if (noteVersions.status === 'fulfilled') setMinutesVersions(noteVersions.value)
  }
  useEffect(() => {
    void reload().catch(e => setError(e.message))
    return () => { loadSequence.current++ }
  }, [id])
  useEffect(() => {
    if (!editing && currentVersion && currentVersion !== meeting?.minutes_revision) void reload().catch(e => setError(e.message))
  }, [currentVersion, editing])
  async function perform(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await work() } catch (e) {
      const message = (e as Error).message
      setError(message.includes('REVISION_CONFLICT')
        ? '다른 탭 또는 작업에서 내용이 바뀌었습니다. 입력은 유지했습니다. 필요한 내용을 복사한 뒤 최신 내용 불러오기를 눌러주세요.' : message)
    } finally { setBusy(false) }
  }
  const editable = !!minutes && minutes.id === meeting?.minutes_revision
  function change(value: Content) { setDraft(value); setDirty(true) }
  function updateTopic(index: number, value: Topic) {
    if (draft) change({ ...draft, topics: draft.topics.map((t, i) => i === index ? value : t) })
  }
  function moveTopic(index: number, direction: number) {
    if (!draft) return
    const values = [...draft.topics]
    ;[values[index], values[index + direction]] = [values[index + direction], values[index]]
    change({ ...draft, topics: values })
  }
  function changeItem(section: Section, index: number, value: Item) {
    if (draft) change({ ...draft, [section]: draft[section].map((item, i) => i === index ? value : item) })
  }
  function jump(ms: number) {
    if (!audio.current) return
    audio.current.currentTime = ms / 1000
    void audio.current.play().catch(() => setNotice('재생기에서 재생 버튼을 눌러주세요.'))
  }
  const audioPlayer = audioAvailable ? <audio ref={audio} controls preload="metadata" src={`/api${base}/audio`} aria-label="회의 음성" /> : <p>음성을 사용할 수 없어 재생이 제한됩니다. 결과와 저장된 논제 시각은 유지됩니다.</p>
  return <div className="detail-workspace">
    <div className="toolbar"><button disabled={busy} className="secondary" onClick={async () => { if (await confirmNavigation()) void perform(reload) }}>최신 내용 불러오기</button>{headerActions}{notice && <span role="status">{notice}</span>}</div>
    {error && <p role="alert" className="error">{error}</p>}
    <section aria-label="회의록">
      {minutes && draft ? <>
        <div className="minutes-heading"><h3>회의록</h3></div>
        <div className="toolbar minutes-controls">
          <label>회의록 버전<select aria-label="회의록 버전" disabled={busy} value={minutes.id} onChange={async e => {
            const version = e.target.value
            if (await confirmNavigation()) void perform(async () => {
              const value = await api<Minutes>(`${base}/minutes?version=${encodeURIComponent(version)}`)
              setMinutes(value); setDraft(value.document); setDirty(false); setEditing(false)
            })
          }}>{minutesVersions.map(v => <option key={v.id} value={v.id}>{versionLabel(v)}</option>)}</select></label>
          {!editing && <button disabled={busy || !editable} onClick={() => setEditing(true)}>회의록 편집</button>}
        </div>
        {!minutes.source_available && <p role="status">원문 전사는 만료됐거나 사용할 수 없습니다. 결과 열람·편집·저장·내보내기는 가능합니다.</p>}
        {minutes.source_available && minutes.stale_transcript && <p role="status">이 회의록은 이전 전사를 바탕으로 작성됐습니다.</p>}
        {audioPlayer}
        <h4>회의 개요·요약</h4>
        {draft.metadata.occurred_at && <p>{new Date(draft.metadata.occurred_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })} KST</p>}
        {editing ? <label>요약<textarea aria-label="요약" rows={5} maxLength={20000} disabled={busy} value={draft.summary} onChange={e => change({ ...draft, summary: e.target.value })} /></label> : <p className="document-prose">{sentenceLines(draft.summary)}</p>}
        <h4>주요 논제</h4>
        <div className="topic-list">{draft.topics.map((topic, index) => <article className="topic-card" key={topic.id}>
          {editing ? <fieldset disabled={busy}>
            <label>논제 제목 {index + 1}<input maxLength={300} value={topic.title} onChange={e => updateTopic(index, { ...topic, title: e.target.value })} /></label>
            <label>논의 내용 {index + 1}<textarea rows={3} maxLength={10000} value={topic.text} onChange={e => updateTopic(index, { ...topic, text: e.target.value })} /></label>
            <label>현재 결론 {index + 1}<textarea maxLength={10000} value={topic.conclusion} onChange={e => updateTopic(index, { ...topic, conclusion: e.target.value })} /></label>
            <label>결론 구분 {index + 1}<select value={topic.conclusion_kind} onChange={e => updateTopic(index, { ...topic, conclusion_kind: e.target.value })}><option value="discussion">논의</option><option value="agreement">합의</option><option value="proposal">제안</option><option value="deferred">보류</option></select></label>
            <div className="toolbar topic-actions"><button aria-label="논제 위로" title="논제 위로" disabled={index === 0} onClick={() => moveTopic(index, -1)}><span aria-hidden="true">↑</span></button><button aria-label="논제 아래로" title="논제 아래로" disabled={index === draft.topics.length - 1} onClick={() => moveTopic(index, 1)}><span aria-hidden="true">↓</span></button><button onClick={() => change({ ...draft, topics: draft.topics.filter((_, i) => i !== index) })}>논제 삭제</button></div>
          </fieldset> : <><h5>{topic.title || `논제 ${index + 1}`}</h5><p className="document-prose">{sentenceLines(topic.text)}</p>{topic.conclusion && <p className="topic-conclusion document-prose">{({ agreement: '합의', proposal: '제안', deferred: '보류', discussion: '논의' } as Record<string, string>)[topic.conclusion_kind]} · {sentenceLines(topic.conclusion)}</p>}</>}
          <div className="toolbar">{topic.starts.map((start, i) => <button key={`${start.start_ms}-${i}`} disabled={!audioAvailable} onClick={() => jump(start.start_ms)}>{timestamp(start.start_ms)} {i === 0 ? '논의 시작' : '재논의'}</button>)}</div>
        </article>)}</div>
        {editing && <button className="add-topic" disabled={busy || draft.topics.length >= 200} onClick={() => change({ ...draft, topics: [...draft.topics, { id: crypto.randomUUID(), title: '', text: '', conclusion: '', conclusion_kind: 'discussion', starts: [], source_segment_ids: [], origin: 'user_authored' }] })}>논제 추가</button>}
        <p className="document-notice">자동 정리된 내용입니다. 구체적인 결정·담당자·기한은 직접 확인하고 보완하세요.</p>
        {sections.map(([section, label]) => <section key={section}><h4>{label}</h4>
          {draft[section].map((item, index) => <div className="minutes-item" key={item.id}>{editing ? <fieldset disabled={busy}>
            <label>{label} 내용<textarea aria-label={`${label} 내용`} maxLength={10000} value={item.text ?? item.task ?? ''} onChange={e => changeItem(section, index, { ...item, [section === 'action_items' ? 'task' : 'text']: e.target.value })} /></label>
            {section === 'action_items' && <div className="toolbar"><label>담당자<input maxLength={200} value={item.owner_name || ''} onChange={e => changeItem(section, index, { ...item, owner_name: e.target.value || null })} /></label><label>기한<input type="date" value={item.due_date || ''} onChange={e => changeItem(section, index, { ...item, due_date: e.target.value || null })} /></label></div>}
            <button onClick={() => change({ ...draft, [section]: draft[section].filter((_, i) => i !== index) })}>항목 삭제</button>
          </fieldset> : <><p className="document-prose">{sentenceLines(item.text ?? item.task)}</p>{section === 'action_items' && <p>담당: {item.owner_name || '미정'} · 기한: {item.due_date || item.due_date_original_expression || '미정'}</p>}</>}</div>)}
          {!draft[section].length && !editing && <p>없음</p>}
          {editing && <button disabled={busy || draft[section].length >= 200} onClick={async () => {
            const common = { id: crypto.randomUUID(), source_segment_ids: [], review_status: 'user_authored' }
            const item = section === 'action_items' ? { ...common, task: '', owner_name: null, owner_speaker_id: null, due_date: null, due_date_original_expression: null } : { ...common, text: '' }
            change({ ...draft, [section]: [...draft[section], item] })
          }}>{label} 직접 추가</button>}
        </section>)}
        <div className="toolbar">{editing && <><button className="primary" disabled={busy || !dirty} onClick={() => void perform(async () => {
          await api(`${base}/minutes/${minutes.id}`, { method: 'PATCH', body: JSON.stringify({ expected_revision: meeting!.revision, content: draft }) })
          await reload(); setNotice('새 회의록 초안을 저장했습니다.')
        })}>수정본 저장</button><button disabled={busy} onClick={async () => { if (await confirmNavigation()) { setDraft(minutes.document); setDirty(false); setEditing(false) } }}>변경 취소</button></>}
        </div>
        {dirty && <p role="status">저장하지 않은 변경이 있습니다.</p>}
        {!editable && <p>과거 버전은 읽기 전용입니다. 최신 내용을 불러오면 편집할 수 있습니다.</p>}
      </> : <>{audioPlayer}<p>회의록이 아직 없습니다.</p></>}
    </section>

    <div className="generation-box"><h3>AI를 사용한 요약 회의록 생성</h3><div className="toolbar">
      <button className="generation-action" disabled={busy || dirty || !meeting?.transcript_version} onClick={async () => {
        if (meeting!.minutes_revision && !(await confirmAction('현재 전사로 초안을 재생성할까요? 기존 버전과 수정본은 보존됩니다.'))) return
        void perform(async () => {
        generationKey.current ||= crypto.randomUUID()
        await api(`${base}/minutes/generate`, { method: 'POST', headers: { 'Idempotency-Key': generationKey.current }, body: JSON.stringify({ expected_revision: meeting!.revision, transcript_version: meeting!.transcript_version, allow_external_text: true, new_draft: !!meeting!.minutes_revision }) })
        generationKey.current = null; await reload(); setNotice('AI 요약 작업을 등록했습니다. 완료되면 새 버전을 표시합니다.')
      })
      }}>{meeting?.minutes_revision ? '초안 재생성' : '회의록 생성'}</button>
      {minutes && <><a className="generation-action" href={`/api${base}/export?format=txt&version=${encodeURIComponent(minutes.id)}`}>텍스트 내보내기</a><a className="generation-action" href={`/api${base}/export?format=md&version=${encodeURIComponent(minutes.id)}`}>Markdown 내보내기</a></>}
      </div>
    </div>
  </div>
}
