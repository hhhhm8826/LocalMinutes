import { useEffect, useState } from 'react'
import { api } from './api'

type Diagnostics = {
  version: string; database_revision: string; threads: number; worker_alive: boolean
  storage: { data_dir: string; cache_dir: string; config_dir: string; disk_free_bytes: number }
  model_cache: { models: { name: string; files_present: boolean; missing_files: string[] }[] }
  runtime: { path: string; home: string; configured_model: string; version: string | null; login: string; error: string | null; configured_model_in_local_catalog: boolean | null; checked_at: number }
  usage: { reserved_calls: number; completed_calls: number; input_tokens: number; cached_input_tokens: number; output_tokens: number; last_observed_model: string | null }
}
export function DiagnosticsPanel() {
  const [value, setValue] = useState<Diagnostics | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function refresh() {
    setBusy(true); setError('')
    try { setValue(await api<Diagnostics>('/diagnostics')) } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  useEffect(() => { void refresh() }, [])
  return <section className="settings-card"><h2>실행 환경과 사용량</h2><button className="secondary" disabled={busy} onClick={() => void refresh()}>{busy ? '확인 중…' : '진단 새로고침'}</button>
    {error && <p role="alert" className="error">{error}</p>}
    {value && <><dl><dt>앱 / 데이터베이스</dt><dd>{value.version} / {value.database_revision}</dd><dt>처리 장치</dt><dd>CPU · {value.threads} 스레드</dd><dt>작업 관리자</dt><dd>{value.worker_alive ? '실행 중' : '정지됨'}</dd>
      <dt>저장 위치</dt><dd>{value.storage.data_dir}</dd><dt>앱 캐시</dt><dd>{value.storage.cache_dir}</dd><dt>설정 위치</dt><dd>{value.storage.config_dir}</dd><dt>디스크 여유</dt><dd>{(value.storage.disk_free_bytes / 1024 ** 3).toFixed(1)} GiB</dd></dl>
      <h3>모델 캐시</h3><ul>{value.model_cache.models.map(model => <li key={model.name}>{({ asr: '전사', ko_align: '한국어 정렬', en_align: '영어 정렬', diarization: '화자 구분' } as Record<string, string>)[model.name]}: {model.files_present ? '필수 파일 있음' : '파일 준비 필요'}{model.missing_files.length > 0 && <small> · {model.missing_files.join(', ')}</small>}</li>)}</ul>
      <p>캐시 파일 존재 여부이며 모델 추론 성공이나 파일 무결성을 보증하지 않습니다.</p>
      <h3>런타임 Codex</h3><dl><dt>실행 파일</dt><dd>{value.runtime.path}</dd><dt>별도 인증 위치</dt><dd>{value.runtime.home}</dd><dt>CLI 버전</dt><dd>{value.runtime.version || '확인 불가'}</dd><dt>로컬 로그인 상태</dt><dd>{value.runtime.login === 'chatgpt' ? 'ChatGPT 구독 로그인' : value.runtime.login === 'not_logged_in' ? '로그인 필요' : '확인 불가'}</dd><dt>설정 모델</dt><dd>{value.runtime.configured_model}</dd><dt>로컬 모델 목록</dt><dd>{value.runtime.configured_model_in_local_catalog === null ? '확인 불가' : value.runtime.configured_model_in_local_catalog ? '설정 모델 있음' : '설정 모델 없음'}</dd><dt>마지막 호출 모델</dt><dd>{value.usage.last_observed_model || '기록 없음'}</dd></dl>
      {value.runtime.error && <p role="status">진단 코드: {value.runtime.error}</p>}
      <p>로컬 상태만 확인합니다. 실제 접속 권한·남은 구독 한도는 확인할 수 없습니다. CLI 상태는 최대 30초간 재사용합니다.</p>
      <h3>이 앱의 보관 중인 사용 기록</h3><dl><dt>호출 예약 / 완료</dt><dd>{value.usage.reserved_calls} / {value.usage.completed_calls}</dd><dt>입력 / 캐시 입력 토큰</dt><dd>{value.usage.input_tokens} / {value.usage.cached_input_tokens}</dd><dt>출력 토큰</dt><dd>{value.usage.output_tokens}</dd><dt>계정 전체 남은 한도</dt><dd>확인 불가</dd></dl><p>회의 삭제 시 해당 사용 기록도 삭제됩니다. 예약 수에는 실패·중단이 포함되며 구독 청구량을 뜻하지 않습니다.</p>
    </>}
  </section>
}
