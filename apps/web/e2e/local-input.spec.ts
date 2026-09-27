import { test, expect } from '@playwright/test'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

test('W4 생성 응답 유실 후 새로고침과 재시도는 같은 자료 사용', async ({ page }) => {
  const directory = await mkdtemp(join(tmpdir(), 'minutes-retry-'))
  const path = join(directory, 'retry.wav')
  const wav = Buffer.alloc(44 + 32000)
  wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16)
  wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28)
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(32000, 40)
  await writeFile(path, wav)
  try {
    await page.goto('/')
    await page.getByRole('button', { name: '새 회의 업로드', exact: true }).click()
    await page.getByLabel('회의 제목', { exact: true }).fill('응답 유실 재시도')
    await page.getByLabel('음성 또는 영상 파일', { exact: true }).setInputFiles(path)
    let firstId = ''
    await page.route('**/api/meetings', async route => {
      if (route.request().method() !== 'POST') { await route.continue(); return }
      firstId = (await (await route.fetch()).json()).id
      await route.abort('connectionreset')
    })
    await page.getByRole('button', { name: '업로드하고 처리 시작', exact: true }).click()
    await expect(page.getByRole('alert')).toBeVisible()
    expect(firstId).not.toBe('')
    await page.unroute('**/api/meetings')
    page.on('dialog', dialog => dialog.accept())
    await page.reload()
    await expect(page.getByLabel('회의 제목', { exact: true })).toHaveValue('응답 유실 재시도')
    await page.getByLabel('음성 또는 영상 파일', { exact: true }).setInputFiles(path)
    const response = page.waitForResponse(r => r.url().endsWith('/api/meetings') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '같은 업로드 재시도', exact: true }).click()
    expect((await (await response).json()).id).toBe(firstId)
    await expect(page.getByText('입력을 등록했습니다.', { exact: false })).toBeVisible()
    const meetings = await (await page.request.get('/api/meetings')).json()
    expect(meetings.filter((meeting: { title: string }) => meeting.title === '응답 유실 재시도')).toHaveLength(1)
  } finally { await rm(directory, { recursive: true, force: true }) }
})

test('W4 소유자 권한 분리·로그아웃·다른 탭과 편집 보존', async ({ page, context }) => {
  await page.goto('/')
  await expect(page.getByRole('button', { name: '새 회의 업로드', exact: true })).toBeVisible()
  expect((await page.request.get('/api/settings/retention')).status()).toBe(401)
  expect((await page.request.get('/api/diagnostics')).status()).toBe(401)
  await page.getByRole('button', { name: '설정 및 진단', exact: false }).click()
  await page.getByLabel('소유자 키', { exact: true }).fill('browser-test-owner-key')
  await page.getByLabel('소유자 키', { exact: true }).press('Enter')
  await expect(page.getByRole('heading', { name: '자료 보관', exact: true })).toBeVisible()
  expect(await page.getByRole('heading', { name: '자료 보관', exact: true }).evaluate(e => e.getBoundingClientRect().top)).toBeLessThan(
    await page.getByRole('heading', { name: '런타임 Codex', exact: true }).evaluate(e => e.getBoundingClientRect().top))
  const other = await context.newPage()
  await other.goto('/')
  await other.getByRole('button', { name: '설정 및 진단', exact: false }).click()
  await expect(other.getByRole('heading', { name: '자료 보관', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '회의록', exact: false }).first().click()
  await page.getByRole('button', { name: '브라우저 검증 회의', exact: true }).click()
  await page.getByRole('button', { name: '회의록 편집', exact: true }).click()
  await page.getByLabel('요약', { exact: true }).fill('로그아웃 중에도 유지할 입력')
  await page.locator('header').getByRole('button', { name: '로그아웃', exact: true }).click()
  await expect(page.getByLabel('요약', { exact: true })).toHaveValue('로그아웃 중에도 유지할 입력')
  await expect(other.getByLabel('소유자 키', { exact: true })).toBeVisible()
  await expect(other.getByRole('heading', { name: '런타임 Codex', exact: true })).toHaveCount(0)
  expect((await page.request.get('/api/settings/retention')).status()).toBe(401)
  expect((await page.request.get('/api/meetings')).status()).toBe(200)
})

for (const zone of ['UTC', 'America/Los_Angeles']) {
  test.describe(`W4 KST ${zone}`, () => {
    test.use({ timezoneId: zone })
    test('자동 입력과 배타 화면·뒤로 가기', async ({ page }) => {
      await page.clock.setFixedTime(new Date('2026-09-27T03:04:00Z'))
      await page.goto('/')
      await page.getByLabel('제목·본문 검색', { exact: true }).fill('브라우저')
      await page.getByRole('button', { name: '새 회의 업로드', exact: true }).click()
      await expect(page.getByLabel('회의 일시 (한국 시간)', { exact: true })).toHaveValue('2026-09-27T12:04')
      await expect(page.getByLabel('언어', { exact: true })).toHaveCount(0)
      await expect(page.getByLabel('실제 발화자 수', { exact: true })).toHaveCount(0)
      await expect(page.getByLabel('연결된 AI를 사용한 회의록 생성', { exact: true })).toBeChecked()
      await expect(page.getByLabel('연결된 AI를 사용한 회의록 생성', { exact: true })).toBeDisabled()
      await expect(page.getByLabel('제목·본문 검색', { exact: true })).toHaveCount(0)
      await expect(page.locator('.meeting-list')).toHaveCount(0)
      await page.goBack()
      await expect(page.getByLabel('제목·본문 검색', { exact: true })).toHaveValue('브라우저')
      await page.getByRole('button', { name: '새 회의 업로드', exact: true }).click()
      await page.getByLabel('회의 제목', { exact: true }).fill(`KST ${zone}`)
      const wav = Buffer.alloc(44 + 32000)
      wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16)
      wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28)
      wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(32000, 40)
      await page.getByLabel('음성 또는 영상 파일', { exact: true }).setInputFiles({ name: 'kst.wav', mimeType: 'audio/wav', buffer: wav })
      const response = page.waitForResponse(r => r.url().endsWith('/api/meetings') && r.request().method() === 'POST')
      await page.getByRole('button', { name: '업로드하고 처리 시작', exact: true }).click()
      const meeting = await (await response).json()
      const options = JSON.parse(meeting.settings_json)
      expect(options.occurred_at).toBe('2026-09-27T12:04:00+09:00')
      expect(options.timezone).toBe('Asia/Seoul')
      expect(options.language).toBe('auto'); expect(options.speakers).toBeNull(); expect(options.allow_external_text).toBe(true)
      await expect(page.getByText('입력을 등록했습니다.', { exact: false })).toBeVisible()
      await expect(page.getByRole('heading', { name: `KST ${zone}`, exact: true })).toBeVisible()
    })
  })
}
