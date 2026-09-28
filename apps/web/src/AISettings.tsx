import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { confirmAction } from './confirm'
import { AIBudget } from './AIBudget'

type ProviderId = 'codex_cli' | 'gemini_api' | 'claude_cli'
type Ready = { ready: boolean; code: string | null; checked_at: number | null }
type Provider = Ready & { id: ProviderId; label: string; model: string }
type Policy = { revision: number; active_provider: ProviderId; providers: Provider[]; gemini_key: { registered: boolean; credential_revision: string | null; updated_at: number | null } }
const reasons: Record<string, string> = {
  AI_AUTH_REQUIRED: '인증이 필요합니다. Gemini 키를 등록하거나 전용 런타임 CLI에 로그인하세요.',
  AI_AUTH_INVALID: '인증을 확인하지 못했습니다. 키 또는 런타임 로그인을 갱신하세요.',
  AI_INSTALL_REQUIRED: 'CLI 설치가 필요합니다. README의 공급자 설치 안내를 확인하세요.',
  AI_CONNECTION_CHECK_REQUIRED: '연결 확인을 실행하세요. 확인 상태는 5분 동안 유효합니다.',
  AI_CHECK_COOLDOWN: '잠시 후 다시 확인하세요. 연결 확인 간격은 최소 10초입니다.',
  AI_MODEL_UNAVAILABLE: '이 모델을 사용할 수 없습니다. 모델 이름과 계정 접근 권한을 확인하세요.',
  AI_MODEL_METADATA_REQUIRED: '지원하는 모델의 문맥 한도 정보가 필요합니다.',
  AI_REVISION_CONFLICT: '다른 탭에서 AI 설정을 변경했습니다. 상태 새로고침 후 다시 선택하세요.',
  AI_CREDENTIAL_REVISION_CONFLICT: '다른 탭에서 키를 변경했습니다. 상태 새로고침 후 다시 입력하세요.',
  AI_CREDENTIALS_CHANGED: '키가 변경되었습니다. 상태를 새로고침하고 다시 연결을 확인하세요.',
  AI_SERVICE_UNAVAILABLE: 'AI 서비스가 일시적으로 요청을 처리하지 못했습니다. 잠시 후 재시도하세요.',
  AI_SERVER_ERROR: 'AI 공급자 서버에서 오류가 발생했습니다. 잠시 후 재시도하세요.',
  AI_NETWORK: '네트워크 연결에 실패했습니다. 연결 상태를 확인하세요.',
  AI_RATE_LIMIT: '서비스 요청 한도에 도달했습니다. 잠시 후 다시 시도하세요.',
  AI_REGION_UNSUPPORTED: '현재 지역에서는 이 서비스를 사용할 수 없습니다.',
  AI_PROVIDER_BUSY: '해당 AI가 작업 중입니다. 잠시 후 확인하세요.',
  AI_SUBSCRIPTION_REQUIRED: '공식 구독 계정으로 전용 런타임에 로그인하세요. API 키 인증은 지원하지 않습니다.',
  AI_CLI_VERSION_UNVERIFIED: '지원하는 고정 CLI 버전과 실행 옵션을 확인하세요.',
  AI_ISOLATION_UNVERIFIED: '런타임 인증·설정 격리를 확인하지 못했습니다. README의 전용 런타임 안내를 확인하세요.',
}
function reason(code: string | null) {
  return code ? reasons[code] || `연결을 사용할 수 없습니다 (${code}). README의 연결 안내를 확인하세요.` : '연결을 확인하세요.'
}

export function AISettings() {
  const [value, setValue] = useState<Policy | null>(null)
  const [models, setModels] = useState<Partial<Record<ProviderId, string>>>({})
  const [checks, setChecks] = useState<Partial<Record<ProviderId, Ready & { model: string }>>>({})
  const [selected, setSelected] = useState<ProviderId>('codex_cli')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [now, setNow] = useState(Date.now())
  const keyInput = useRef<HTMLInputElement>(null)
  const pending = useRef(false)
  const mounted = useRef(false)
  const clearKey = () => { if (keyInput.current) keyInput.current.value = '' }
  async function refresh() {
    const current = await api<Policy>('/settings/ai')
    if (!mounted.current) return
    setValue(current); setSelected(current.active_provider)
    setModels(Object.fromEntries(current.providers.map(p => [p.id, p.model])))
    setChecks(Object.fromEntries(current.providers.map(p => [p.id, p])))
    clearKey(); setNow(Date.now())
  }
  async function run(action: () => Promise<void>) {
    if (pending.current) return
    pending.current = true; setBusy(true); setError(''); setNotice('')
    try { await action() }
    catch (e) { if (mounted.current) setError(reason((e as Error).message)) }
    finally { pending.current = false; if (mounted.current) setBusy(false) }
  }
  useEffect(() => {
    mounted.current = true
    void run(refresh)
    const timer = setInterval(() => setNow(Date.now()), 1000)
    window.addEventListener('minutes-clear-secrets', clearKey)
    window.addEventListener('minutes-owner-expired', clearKey)
    window.addEventListener('pagehide', clearKey)
    return () => { mounted.current = false; clearKey(); clearInterval(timer); window.removeEventListener('minutes-clear-secrets', clearKey); window.removeEventListener('minutes-owner-expired', clearKey); window.removeEventListener('pagehide', clearKey) }
  }, [])
  const available = (id: ProviderId) => {
    const check = checks[id]
    return !!check?.ready && check.model === models[id] && check.checked_at !== null && now / 1000 - check.checked_at < 300
  }
  async function saveSelection() {
    if (!value || !available(selected)) return
    await api('/settings/ai', { method: 'PATCH', body: JSON.stringify({ expected_revision: value.revision, active_provider: selected, model: models[selected] }) })
    await refresh()
    window.dispatchEvent(new Event('minutes-ai-changed'))
    if (mounted.current) setNotice('활성 AI를 저장했습니다. 새로 등록하는 분석·재생성부터 적용됩니다. 기존 대기 작업의 AI는 바뀌지 않습니다.')
  }
  return <section className="settings-card ai-settings" aria-labelledby="ai-settings-title">
    <h2 id="ai-settings-title">AI 연결 및 선택</h2>
    <p>요약·제목 생성에 사용할 AI 하나를 선택합니다. 음성 처리는 로컬에서 진행하며, 선택한 서비스에는 생성에 필요한 전사 텍스트를 보냅니다.</p>
    <AIBudget />
    {value && <>
      <p>현재 AI: <strong>{value.providers.find(p => p.id === value.active_provider)?.label}</strong></p>
      <fieldset disabled={busy}><legend className="sr-only">공급자 연결과 선택</legend>
        <div className="ai-providers">{value.providers.map(provider => {
          const checked = checks[provider.id]
          const ready = available(provider.id)
          return <article className={`ai-provider${selected === provider.id ? ' selected' : ''}`} key={provider.id} aria-label={provider.label}>
            <label className="checkbox"><input type="radio" name="active-ai" value={provider.id} checked={selected === provider.id} disabled={!ready} onChange={() => setSelected(provider.id)} />{provider.label}</label>
            <label htmlFor={`ai-model-${provider.id}`}>모델</label>
            <input id={`ai-model-${provider.id}`} value={models[provider.id] || ''} maxLength={100} pattern="[a-zA-Z0-9._-]+" spellCheck={false} onChange={event => setModels({ ...models, [provider.id]: event.target.value })} />
            <p className={ready ? 'ai-ready' : ''}>{ready ? '사용 가능' : reason(checked?.model === models[provider.id] && !checked?.ready ? checked?.code ?? null : 'AI_CONNECTION_CHECK_REQUIRED')}</p>
            <button className="secondary" disabled={!/^[a-zA-Z0-9._-]{1,100}$/.test(models[provider.id] || '')} onClick={() => void run(async () => {
              const model = models[provider.id]!
              const result = await api<Ready>(`/settings/ai/${provider.id}/check`, { method: 'POST', body: JSON.stringify({ model }) })
              if (mounted.current) { setChecks(previous => ({ ...previous, [provider.id]: { ...result, model } })); setNow(Date.now()) }
            })}>연결 확인</button>
          </article>
        })}</div>
        <div className="ai-actions"><button className="primary" disabled={!available(selected)} onClick={() => void run(saveSelection)}>활성 AI 저장</button></div>
      </fieldset>
      <form autoComplete="off" onSubmit={event => {
        event.preventDefault()
        if (pending.current || !keyInput.current?.value.trim()) return
        const body = JSON.stringify({ key: keyInput.current.value, expected_credential_revision: value.gemini_key.credential_revision })
        clearKey()
        void run(async () => {
          await api('/settings/ai/gemini-key', { method: 'PUT', body })
          await refresh()
          window.dispatchEvent(new Event('minutes-ai-changed'))
          if (mounted.current) setNotice('키를 저장했습니다. Gemini 연결 확인 후 활성 AI로 선택하세요.')
        })
      }}><fieldset disabled={busy}>
        <h3>Gemini API 키</h3>
        <p>{value.gemini_key.registered ? '등록됨' : '미등록'}{value.gemini_key.updated_at ? ` · 갱신 ${new Date(value.gemini_key.updated_at * 1000).toLocaleString('ko-KR')}` : ''}</p>
        <label htmlFor="gemini-key">새 API 키</label><input ref={keyInput} id="gemini-key" type="password" autoComplete="new-password" spellCheck={false} maxLength={4096} aria-describedby="gemini-key-help" />
        <p id="gemini-key-help">입력한 키는 이 컴퓨터에만 보관합니다. 저장된 키를 다시 표시하거나 복사하는 기능은 없습니다.</p>
        <div className="ai-actions"><button className="secondary" type="button" disabled={!value.gemini_key.registered} onClick={() => void run(async () => {
          if (!await confirmAction('Gemini 키를 삭제할까요? Gemini를 사용하는 대기 작업은 인증이 준비될 때까지 생성할 수 없습니다.')) return
          clearKey()
          await api('/settings/ai/gemini-key', { method: 'DELETE', body: JSON.stringify({ expected_credential_revision: value.gemini_key.credential_revision }) })
          await refresh(); window.dispatchEvent(new Event('minutes-ai-changed'))
          if (mounted.current) setNotice('Gemini 키를 삭제했습니다.')
        })}>키 삭제</button><button className="primary" type="submit">키 저장·교체</button></div>
      </fieldset></form>
      <p>Gemini 요금과 데이터 처리 조건은 계정·서비스에 따라 다릅니다. 무료 서비스에는 기밀·민감정보를 보내지 마세요. 이 앱은 결제를 활성화하지 않습니다. <a href="https://ai.google.dev/gemini-api/terms" target="_blank" rel="noreferrer">Gemini 이용 조건</a> · <a href="https://ai.google.dev/gemini-api/docs/pricing" target="_blank" rel="noreferrer">요금 안내</a></p>
      <p>Codex·Claude는 별도 런타임의 공식 구독 로그인으로 연결합니다. 설치와 로그인 방법은 README를 확인하세요. 준비된 AI가 없어도 기존 결과 열람·편집·내보내기와 로컬 음성 처리는 가능합니다.</p>
    </>}
    <button className="secondary" disabled={busy} onClick={() => void run(refresh)}>상태 새로고침</button>
    {busy && <p role="status">설정을 확인하고 있습니다…</p>}
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>
}

export function AIStatus() {
  const [status, setStatus] = useState<{ label: string; ready: boolean } | null>(null)
  useEffect(() => {
    let stopped = false, fetching = false
    const refresh = async () => {
      if (fetching) return
      fetching = true
      try { const value = await api<{ label: string; ready: boolean }>('/ai-status'); if (!stopped) setStatus(value) }
      catch { if (!stopped) setStatus(null) }
      finally { fetching = false }
    }
    void refresh()
    const timer = setInterval(() => void refresh(), 30000)
    window.addEventListener('minutes-ai-changed', refresh)
    window.addEventListener('focus', refresh)
    return () => { stopped = true; clearInterval(timer); window.removeEventListener('minutes-ai-changed', refresh); window.removeEventListener('focus', refresh) }
  }, [])
  return status ? <span className="ai-status">{status.label} · {status.ready ? '생성 가능' : '설정 필요'}</span> : null
}
