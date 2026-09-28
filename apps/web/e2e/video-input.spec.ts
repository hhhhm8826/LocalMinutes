import { test, expect } from '@playwright/test'

test('영상 목록 분리와 자동 파일 입력', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('button', { name: '브라우저 검증 회의', exact: true })).toBeVisible()
  await page.getByRole('navigation').getByRole('button', { name: '영상 요약', exact: true }).click()
  await expect(page.getByRole('heading', { name: '영상 요약', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '브라우저 검증 회의', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '새 영상 요약', exact: true }).click()
  await expect(page.getByLabel('영상 제목', { exact: true })).toBeVisible()
  await expect(page.getByLabel('회의 일시 (한국 시간)', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('연결된 AI를 사용한 요약본 생성', { exact: true })).toBeChecked()
  await expect(page.getByLabel('제목·본문 검색', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '뒤로가기', exact: true }).click()
  await expect(page.getByRole('heading', { name: '영상 요약', exact: true })).toBeVisible()
})

test('YouTube 링크는 다운로드 대기열에 등록하고 선택 자료 표시', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button', { name: '영상 요약', exact: true }).click()
  await page.getByRole('button', { name: 'YouTube 영상 요약', exact: true }).click()
  await page.getByLabel('YouTube 링크', { exact: true }).fill('https://youtu.be/AbCde_123-4?t=50')
  await page.getByLabel('제목 (선택)', { exact: true }).fill('YouTube 등록 fixture')
  const response = page.waitForResponse(r => r.url().endsWith('/api/videos/youtube'))
  await page.getByRole('button', { name: '요약 작업 등록', exact: true }).click()
  expect((await response).status()).toBe(202)
  await expect(page.getByRole('heading', { name: 'YouTube 등록 fixture', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '회의록 편집', exact: true })).toHaveCount(0)
})

test('만료된 영상은 읽기 전용 요약과 전체 원본 링크와 최신 요약 유지', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button', { name: '영상 요약', exact: true }).click()
  await page.getByRole('button', { name: '원문 만료 영상', exact: true }).click()
  await expect(page.getByText('발표자가 매출과 전망을 설명했다.', { exact: true })).toBeVisible()
  await expect(page.getByText('01:00:01 · 음성 없음', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '원본 영상', exact: true })).toHaveAttribute('href', 'https://www.youtube.com/watch?v=AbCde_123-4')
  await expect(page.getByRole('button', { name: '요약 재생성', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '회의록 편집', exact: true })).toHaveCount(0)
  await expect(page.locator('textarea')).toHaveCount(0)
  await expect(page.getByLabel('요약본 버전', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '원본 영상', exact: true })).toHaveCount(1)
  await expect(page.getByText('작년 매출 30억 원', { exact: false })).toHaveCount(1)
  await expect(page.getByRole('heading', { name: '주요 수치·주장·전망' })).toHaveCount(0)
  const response = await page.request.get(await page.getByRole('link', { name: '텍스트 내보내기', exact: true }).getAttribute('href') as string)
  expect(response.status()).toBe(200)
  expect(await response.text()).toContain('발표자가 매출과 전망을 설명했다.')
  expect(await response.text()).toContain('작년 매출 30억 원')
  expect(await response.text()).not.toContain('버전 1')
})

test('영상 획득 실패는 이유와 파일 입력 대안을 표시', async ({ page }) => {
  await page.route('**/api/jobs', async route => {
    const response = await route.fetch()
    const jobs = await response.json()
    await route.fulfill({ response, json: jobs.map((job: Record<string, unknown>) => ({
      ...job, state: 'FAILED', source_kind: 'youtube', error_code: 'YOUTUBE_ACCESS_RESTRICTED',
    })) })
  })
  await page.goto('/')
  await expect(page.getByText('공개·일부 공개 영상만 지원하며 로그인·유료 접근·DRM 자료는 처리할 수 없습니다. 허가된 파일을 직접 업로드할 수 있습니다.', { exact: true }).first()).toBeVisible()
})
