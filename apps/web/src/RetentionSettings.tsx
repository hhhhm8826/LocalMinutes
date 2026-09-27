import { confirmAction } from './confirm'
import { useEffect, useState } from 'react'
import { api } from './api'

type Policy = { revision: number; enabled: boolean; media_days: number | null; transcript_days: number | null }
export function RetentionSettings() {
  const [value, setValue] = useState<Policy | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { api<Policy>('/settings/retention').then(setValue).catch(e => setError(e.message)) }, [])
  return <section className="settings-card"><h2>자료 보관</h2><p>기본은 음성·영상 7일, 전사 30일, 결과 문서 영구 보관입니다. 입력 수신 완료일부터 계산하며, 진행 중인 작업은 끝난 뒤 정리합니다. 빈 기간은 계속 보관합니다.</p>
    {value && <form onSubmit={async event => {
      event.preventDefault()
      if (value.enabled && !(await confirmAction('기한이 지난 자료는 자동으로 영구 삭제됩니다. 이 보관 정책을 적용할까요?'))) return
      setBusy(true); setError(''); setNotice('')
      try { setValue(await api<Policy>('/settings/retention', { method: 'PATCH', body: JSON.stringify({ expected_revision: value.revision, enabled: value.enabled, media_days: value.media_days, transcript_days: value.transcript_days }) })); setNotice('보관 정책을 저장했습니다. 작업 관리자가 주기적으로 적용합니다.') }
      catch (e) { setError((e as Error).message) } finally { setBusy(false) }
    }}><fieldset disabled={busy}><label className="checkbox"><input type="checkbox" checked={value.enabled} onChange={e => setValue({ ...value, enabled: e.target.checked })} />자동 삭제 사용</label>
      <div className="toolbar">{([['media_days', '원본·재생용 음성·영상'], ['transcript_days', '전사·정렬·화자 임베딩']] as const).map(([field, label]) => <label key={field}>{label} 보관 일수<input type="number" min={1} max={36500} value={value[field] ?? ''} onChange={e => setValue({ ...value, [field]: e.target.value ? Number(e.target.value) : null })} /></label>)}</div>
      <p>음성 만료 후에는 재생만, 전사 만료 후에는 원문 확인·재생성만 제한됩니다. 회의록·요약본과 버전은 남으며 회의록 편집·확정·내보내기는 계속 사용할 수 있습니다. 별도로 만든 백업·내보낸 파일은 자동 삭제 대상이 아닙니다.</p>
      <button className="primary">보관 정책 저장</button></fieldset></form>}
    {error && <p role="alert" className="error">{error} · 다른 탭에서 정책을 바꿨다면 설정 화면을 다시 열어주세요.</p>}{notice && <p role="status">{notice}</p>}
  </section>
}
