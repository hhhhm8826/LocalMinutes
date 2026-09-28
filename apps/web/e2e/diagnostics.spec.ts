import { test, expect } from '@playwright/test'

test('런타임 진단은 중복 모델 항목 없이 Codex와 Claude 상태 표시', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '설정 및 진단' }).click()
  await page.getByLabel('소유자 키', { exact: true }).fill('browser-test-owner-key')
  await page.getByRole('button', { name: '설정 열기' }).click()
  const panel = page.locator('.settings-card').filter({ has: page.getByRole('heading', { name: '실행 환경과 사용량' }) })
  await expect(panel.getByRole('heading', { name: '런타임 Codex' })).toBeVisible()
  await expect(panel.getByRole('heading', { name: '런타임 Claude' })).toBeVisible()
  for (const label of ['설정 모델', '로컬 모델 목록', '마지막 호출 모델']) {
    await expect(panel.getByText(label, { exact: true })).toHaveCount(0)
  }
  await expect(panel.locator('dt').filter({ hasText: /^CLI 버전$/ })).toHaveCount(2)
  await expect(panel.locator('dt').filter({ hasText: /^로컬 로그인 상태$/ })).toHaveCount(2)
})
