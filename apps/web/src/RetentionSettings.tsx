import { useEffect, useState } from 'react'
import { api } from './api'

type Policy = { revision: number; enabled: boolean; original_days: number | null; audio_days: number | null; text_days: number | null }
export function RetentionSettings() {
  const [value, setValue] = useState<Policy | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { api<Policy>('/settings/retention').then(setValue).catch(e => setError(e.message)) }, [])
  return <section className="settings-card"><h2>자료 보관</h2><p>기본은 직접 삭제할 때까지 유지합니다. 자동 삭제를 켜면 업로드한 날부터 계산하며, 진행 중인 작업은 끝난 뒤 정리합니다. 빈 기간은 계속 보관합니다.</p>
    {value && <form onSubmit={async event => {
      event.preventDefault()
      if (value.enabled && !window.confirm('기한이 지난 자료는 자동으로 영구 삭제됩니다. 이 보관 정책을 적용할까요?')) return
      setBusy(true); setError(''); setNotice('')
      try { setValue(await api<Policy>('/settings/retention', { method: 'PATCH', body: JSON.stringify({ expected_revision: value.revision, enabled: value.enabled, original_days: value.original_days, audio_days: value.audio_days, text_days: value.text_days }) })); setNotice('보관 정책을 저장했습니다. 작업 관리자가 주기적으로 적용합니다.') }
      catch (e) { setError((e as Error).message) } finally { setBusy(false) }
    }}><fieldset disabled={busy}><label className="checkbox"><input type="checkbox" checked={value.enabled} onChange={e => setValue({ ...value, enabled: e.target.checked })} />자동 삭제 사용</label>
      <div className="toolbar">{([['original_days', '원본 음성·영상'], ['audio_days', '재생용 추출 음성'], ['text_days', '전사·회의록·화자 임베딩']] as const).map(([field, label]) => <label key={field}>{label} 보관 일수<input type="number" min={1} max={36500} value={value[field] ?? ''} onChange={e => setValue({ ...value, [field]: e.target.value ? Number(e.target.value) : null })} /></label>)}</div>
      <p>재생용 음성만 삭제하면 근거 텍스트는 유지됩니다. 전사·회의록이 삭제된 후에는 새 회의로 원본 파일을 업로드해 다시 처리할 수 있습니다.</p>
      <button className="primary">보관 정책 저장</button></fieldset></form>}
    {error && <p role="alert" className="error">{error} · 다른 탭에서 정책을 바꿨다면 설정 화면을 다시 열어주세요.</p>}{notice && <p role="status">{notice}</p>}
  </section>
}
