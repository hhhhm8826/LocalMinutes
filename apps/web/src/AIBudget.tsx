import { useEffect, useState } from 'react'
import { api } from './api'

type Budget = { mode?: string; request_limit?: number; requests_per_minute?: number; used_requests?: number; remaining_requests?: number; available_at?: number; token_limit: number; revision: number; used_tokens: number; reserved_tokens: number; remaining_tokens: number; exhausted: boolean; refills_at: number }
function ProviderBudget({ provider, label }: { provider: string; label: string }) {
  const [budget, setBudget] = useState<Budget | null>(null)
  const daily = provider === 'gemini_api'
  const [minute, setMinute] = useState('')
  const [limit, setLimit] = useState('')
  const [revision, setRevision] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    let stopped = false, fetching = false
    const refresh = async () => {
      if (fetching) return
      fetching = true
      try {
        const value = await api<Budget>(`/settings/ai/budget/${provider}`)
        if (!stopped) {
          setBudget(value)
          setMinute(previous => previous || String(value.requests_per_minute ?? 2))
          setRevision(previous => previous ?? value.revision)
          setLimit(previous => previous || String(daily ? value.request_limit : value.token_limit))
        }
      } catch (e) { if (!stopped) setError((e as Error).message) }
      finally { fetching = false }
    }
    void refresh()
    const timer = setInterval(() => void refresh(), 10000)
    return () => { stopped = true; clearInterval(timer) }
  }, [provider])
  async function save() {
    if (busy || revision === null) return
    setBusy(true); setError(''); setMessage('')
    try {
      const value = await api<Budget>(`/settings/ai/budget/${provider}`, { method: 'PATCH', body: JSON.stringify({ expected_revision: revision, ...(daily ? { request_limit: Number(limit), requests_per_minute: Number(minute) } : { token_limit: Number(limit) }) }) })
      setBudget(value); setRevision(value.revision); setLimit(String(daily ? value.request_limit : value.token_limit))
      setMinute(String(value.requests_per_minute ?? 2))
      setMessage('한도를 저장했습니다. 예산이 있으면 대기 중인 AI 작업이 자동으로 재개됩니다.')
    } catch (e) {
      setError((e as Error).message === 'AI_BUDGET_REVISION_CONFLICT' ? '다른 설정 화면에서 이 공급자의 한도를 변경했습니다. 최신 한도를 불러온 뒤 다시 저장하세요.' : '한도를 저장하지 못했습니다.')
    } finally { setBusy(false) }
  }
  return <section aria-label={`${label} ${daily ? "일일 요청 예산" : "주간 예산"}`}>
    <h4>{label}</h4>
    {budget && <>
      {daily ? <p>오늘 사용 {budget.used_requests} · 남음 {budget.remaining_requests}회 · 분당 최대 {budget.requests_per_minute}회</p> : <p>사용 {budget.used_tokens.toLocaleString('ko-KR')} · 예약·미확정 {budget.reserved_tokens.toLocaleString('ko-KR')} · 남음 {budget.remaining_tokens.toLocaleString('ko-KR')} 토큰</p>}
      <p>다음 갱신: {new Date(budget.refills_at * 1000).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })}</p>
      {budget.exhausted && <p role="status">이 공급자의 한도 소진으로 AI 작업이 대기합니다. 한도 인상 또는 사용 가능 시각 이후 자동 재개됩니다.</p>}
      <form onSubmit={event => { event.preventDefault(); void save() }}>
        <label htmlFor={`weekly-ai-limit-${provider}`}>{daily ? '일일 요청 한도' : '주간 토큰 한도'}</label>
        <input id={`weekly-ai-limit-${provider}`} type="number" min="1" max={daily ? "1000000" : "1000000000000"} step="1" required value={limit} onChange={event => setLimit(event.target.value)} disabled={busy} />
        {daily && <label>분당 최대 요청<input type="number" min="1" max="10000" step="1" required value={minute} onChange={event => setMinute(event.target.value)} disabled={busy} /></label>}
        <div className="ai-actions"><button className="secondary" type="button" disabled={busy} onClick={() => { setLimit(String(daily ? budget.request_limit : budget.token_limit)); setMinute(String(budget.requests_per_minute ?? 2)); setRevision(budget.revision); setError('') }}>최신 한도 불러오기</button><button className="primary" disabled={busy}>{daily ? '요청 한도 저장' : '주간 한도 저장'}</button></div>
      </form>
    </>}
    {daily ? <p>태평양 시간 자정에 갱신됩니다. 실패·재시도·분할 생성도 각각 1회입니다. 3.6 무료 한도 실측치의 절반을 참고한 기본값이며 실제 프로젝트 한도와 다를 수 있습니다.</p> : <p>진행 중인 호출 하나는 한도를 초과해도 결과를 보존합니다. 이후 호출은 대기하며, 사용량을 확인하지 못한 호출은 예약량을 유지합니다.</p>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
  </section>
}


export function AIBudget() {
  return <section aria-labelledby="ai-budget-title">
    <h3 id="ai-budget-title">AI 사용 예산</h3>
    <p>Codex·Claude는 주간 토큰 예산을 한국 시간 월요일 0시에 갱신합니다. Gemini는 별도 일일·분당 요청 한도를 사용합니다.</p>
    <ProviderBudget provider="codex_cli" label="Codex CLI" />
    <ProviderBudget provider="claude_cli" label="Claude CLI" />
    <ProviderBudget provider="gemini_api" label="Gemini API" />
  </section>
}
