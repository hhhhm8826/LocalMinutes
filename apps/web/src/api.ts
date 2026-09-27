let csrf = ''
export function setCsrf(value: string) { csrf = value }
export function uploadFile(meeting: string, file: File, key: string, progress: (value: number, loaded: number, total: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('PUT', `/api/meetings/${meeting}/media?filename=${encodeURIComponent(file.name)}`)
    request.setRequestHeader('X-CSRF-Token', csrf)
    request.setRequestHeader('Idempotency-Key', key)
    request.upload.onprogress = event => { if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100), event.loaded, event.total) }
    request.onerror = () => reject(new Error('연결이 끊겼습니다. 같은 파일로 업로드를 다시 시도할 수 있습니다.'))
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) resolve()
      else {
        let message = '파일 업로드에 실패했습니다.'
        try { const body = JSON.parse(request.responseText); if (typeof body.detail === 'string') message = body.detail } catch { /* Empty error response. */ }
        reject(new Error(message))
      }
    }
    request.send(file)
  })
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && typeof options.body === 'string') headers.set('Content-Type', 'application/json')
  if (options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf)
  const response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin' })
  const body = await response.json()
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '요청을 처리하지 못했습니다.')
  return body as T
}
