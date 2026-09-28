import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'

async function setup(page: Page) {
  let policy = { revision: 1, active_provider: 'codex_cli', providers: [
    { id: 'codex_cli', label: 'Codex CLI', model: 'test-codex', ready: true, code: null, checked_at: Date.now() / 1000 },
    { id: 'gemini_api', label: 'Gemini API', model: 'gemini-3.5-flash', ready: false, code: 'AI_AUTH_REQUIRED', checked_at: null as number | null },
    { id: 'claude_cli', label: 'Claude CLI', model: 'claude-sonnet-4-6', ready: false, code: 'AI_AUTH_REQUIRED', checked_at: null as number | null },
  ], gemini_key: { registered: false, credential_revision: null as string | null, updated_at: null as number | null } }
  const mutations: { method: string; path: string; body: Record<string, unknown> }[] = []
  await page.route('**/api/ai-status', route => route.fulfill({ json: { label: 'Codex CLI', ready: false } }))
  await page.route('**/api/diagnostics', route => route.fulfill({ json: null }))
  await page.route('**/api/settings/ai**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (path.includes('/budget/')) { await route.fallback(); return }
    if (request.method() === 'GET') { await route.fulfill({ json: policy }); return }
    const body = request.postDataJSON()
    mutations.push({ method: request.method(), path, body })
    if (path.endsWith('/gemini-key')) {
      policy.gemini_key = { registered: request.method() === 'PUT', credential_revision: request.method() === 'PUT' ? 'a'.repeat(32) : null, updated_at: Date.now() / 1000 }
      policy.providers[1].ready = false
      policy.providers[1].code = 'AI_CONNECTION_CHECK_REQUIRED'
      await route.fulfill({ json: policy.gemini_key })
    } else if (path.endsWith('/check')) {
      const p = policy.providers.find(p => path.includes(p.id))!
      Object.assign(p, { ready: true, model: body.model, code: null, checked_at: Date.now() / 1000 })
      await route.fulfill({ json: p })
    } else {
      policy = { ...policy, active_provider: body.active_provider, revision: policy.revision + 1 }
      await route.fulfill({ json: policy })
    }
  })
  await page.goto('/')
  await page.getByRole('button', { name: '설정 및 진단' }).click()
  await page.getByLabel('소유자 키', { exact: true }).fill('browser-test-owner-key')
  await page.getByRole('button', { name: '설정 열기' }).click()
  await expect(page.getByLabel('새 API 키', { exact: true })).toBeVisible()
  return mutations
}

test('AI 설정 순서·키 비영속·저장 후 연결·새 요청 선택·삭제 모달', async ({ page }) => {
  const mutations = await setup(page)
  const headings = await page.locator('.settings-card h2').allTextContents()
  expect(headings).toEqual(['AI 연결 및 선택', '자료 보관', '실행 환경과 사용량'])
  const gemini = page.getByRole('article', { name: 'Gemini API' })
  await expect(gemini.getByRole('radio')).toBeDisabled()
  await expect(page.getByRole('article', { name: 'Claude CLI' }).getByRole('radio')).toBeDisabled()
  const key = page.getByLabel('새 API 키', { exact: true })
  await expect(key).toHaveAttribute('type', 'password')
  await key.fill('fake-browser-key-only')
  await page.getByRole('button', { name: '키 저장·교체' }).click()
  await expect(key).toHaveValue('')
  await expect(page.getByText('키를 저장했습니다.', { exact: false })).toBeVisible()
  expect(mutations.filter(m => m.method === 'PUT')).toHaveLength(1)
  await expect(gemini.getByRole('radio')).toBeDisabled()
  await gemini.getByRole('button', { name: '연결 확인' }).click()
  await gemini.getByRole('radio').check()
  await page.getByRole('button', { name: '활성 AI 저장' }).click()
  await expect(page.getByText('활성 AI를 저장했습니다.', { exact: false })).toBeVisible()
  expect(mutations.find(m => m.method === 'PATCH')?.body).toEqual({ expected_revision: 1, active_provider: 'gemini_api', model: 'gemini-3.5-flash' })
  expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))).not.toContain('fake-browser-key-only')
  expect(await page.locator('body').innerText()).not.toContain('fake-browser-key-only')
  await page.getByRole('button', { name: '키 삭제', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '취소', exact: true }).click()
  expect(mutations.filter(m => m.method === 'DELETE')).toHaveLength(0)
  await page.getByRole('button', { name: '키 삭제', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '확인', exact: true }).click()
  await expect(page.getByText('Gemini 키를 삭제했습니다.', { exact: true })).toBeVisible()
  await expect(gemini.getByRole('radio')).toBeDisabled()
})

test('화면 이탈·로그아웃·세션 만료는 입력을 지우고 이전 키를 복원하지 않음', async ({ page }) => {
  await setup(page)
  const key = page.getByLabel('새 API 키', { exact: true })
  await key.fill('fake-unsaved-secret')
  await page.getByRole('navigation').getByRole('button', { name: '회의록', exact: false }).click()
  await page.getByRole('button', { name: '설정 및 진단' }).click()
  await expect(key).toHaveValue('')
  await key.fill('fake-logout-secret')
  await page.getByRole('button', { name: '로그아웃', exact: true }).click()
  await expect(key).toHaveCount(0)
  await page.getByLabel('소유자 키', { exact: true }).fill('browser-test-owner-key')
  await page.getByRole('button', { name: '설정 열기' }).click()
  await expect(key).toHaveValue('')
  await key.fill('fake-expiry-secret')
  await page.route('**/api/settings/ai', route => route.fulfill({ status: 401, json: { detail: 'owner required' } }))
  await page.getByRole('button', { name: '상태 새로고침' }).click()
  await expect(key).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '설정 로그인' })).toBeVisible()
})

test('낡은 탭 충돌·중복 제출 차단·다른 모델과 만료된 확인은 선택 불가', async ({ page }) => {
  const mutations = await setup(page)
  await page.route('**/api/settings/ai', async route => {
    if (route.request().method() !== 'PATCH') { await route.fallback(); return }
    mutations.push({ method: 'PATCH', path: '/api/settings/ai', body: route.request().postDataJSON() })
    await new Promise(resolve => setTimeout(resolve, 150))
    await route.fulfill({ status: 409, json: { detail: 'AI_REVISION_CONFLICT' } })
  })
  await page.getByRole('button', { name: '활성 AI 저장' }).evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click() })
  await expect(page.getByRole('alert')).toContainText('다른 탭')
  expect(mutations.filter(m => m.method === 'PATCH')).toHaveLength(1)
  const codex = page.getByRole('article', { name: 'Codex CLI' })
  await codex.getByLabel('모델', { exact: true }).fill('another-model')
  await expect(codex.getByRole('radio')).toBeDisabled()
  await expect(page.getByRole('button', { name: '활성 AI 저장' })).toBeDisabled()
  await codex.getByLabel('모델', { exact: true }).fill('test-codex')
  await page.clock.install()
  await page.clock.fastForward(301000)
  await expect(codex.getByRole('radio')).toBeDisabled()
  await expect(page.getByRole('button', { name: '활성 AI 저장' })).toBeDisabled()
})
