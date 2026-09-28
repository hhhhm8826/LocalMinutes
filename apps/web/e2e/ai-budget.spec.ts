import { test, expect } from '@playwright/test'

test('공급자별 예산과 Gemini 한도 변경은 독립적', async ({ page }) => {
  const budgets: Record<string, { request_limit?: number; requests_per_minute?: number; used_requests?: number; remaining_requests?: number; token_limit: number; revision: number; used_tokens: number; reserved_tokens: number; remaining_tokens: number; exhausted: boolean; refills_at: number }> = {}
  for (const provider of ['codex_cli', 'claude_cli', 'gemini_api']) {
    const token_limit = provider === 'gemini_api' ? 2000000 : 20000000
    const used_tokens = provider === 'gemini_api' ? 2000100 : 0
    budgets[provider] = { token_limit, revision: 1, used_tokens, reserved_tokens: 0, remaining_tokens: Math.max(0, token_limit-used_tokens), exhausted: used_tokens >= token_limit, refills_at: Date.now()/1000+86400 }
  }
  Object.assign(budgets.gemini_api, { request_limit: 10, requests_per_minute: 2, used_requests: 10, remaining_requests: 0 })
  await page.route('**/api/settings/ai/budget/*', async route => {
    const provider = new URL(route.request().url()).pathname.split('/').pop()!
    if (route.request().method() === 'PATCH') {
      expect(provider).toBe('gemini_api')
      const body = route.request().postDataJSON()
      expect(body.expected_revision).toBe(1)
      expect(body.requests_per_minute).toBe(2)
      expect(body.token_limit).toBeUndefined()
      budgets[provider] = { ...budgets[provider], request_limit: body.request_limit, requests_per_minute: body.requests_per_minute, revision: 2, remaining_requests: body.request_limit-10, exhausted: false }
    }
    await route.fulfill({ json: budgets[provider] })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '설정 및 진단' }).click()
  await page.getByLabel('소유자 키', { exact: true }).fill('browser-test-owner-key')
  await page.getByRole('button', { name: '설정 열기' }).click()
  const gemini = page.getByRole('region', { name: 'Gemini API 일일 요청 예산', exact: true })
  const codex = page.getByRole('region', { name: 'Codex CLI 주간 예산', exact: true })
  const claude = page.getByRole('region', { name: 'Claude CLI 주간 예산', exact: true })
  await expect(gemini.getByLabel('일일 요청 한도')).toHaveValue('10')
  await expect(codex.getByLabel('주간 토큰 한도')).toHaveValue('20000000')
  await expect(claude.getByLabel('주간 토큰 한도')).toHaveValue('20000000')
  await expect(gemini.getByText('한도 소진으로', { exact: false })).toBeVisible()
  await gemini.getByLabel('일일 요청 한도').fill('12')
  await gemini.getByRole('button', { name: '요청 한도 저장', exact: true }).click()
  await expect(gemini.getByText('한도를 저장했습니다.', { exact: false })).toBeVisible()
  await expect(gemini.getByText('남음 2회', { exact: false })).toBeVisible()
  await expect(codex.getByLabel('주간 토큰 한도')).toHaveValue('20000000')
  await expect(claude.getByLabel('주간 토큰 한도')).toHaveValue('20000000')
})
