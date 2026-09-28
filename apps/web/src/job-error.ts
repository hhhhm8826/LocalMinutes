const messages: Record<string, string> = {
  AI_DAILY_REQUEST_BUDGET_EXHAUSTED: 'Gemini 일일 요청 한도 소진으로 대기 중입니다. 한도를 늘리거나 태평양 시간 자정에 갱신되면 자동 재개합니다.',
  AI_REQUEST_RATE_WAIT: 'Gemini 분당 요청 간격을 기다리고 있습니다. 자동으로 재개합니다.',
  AI_WEEKLY_BUDGET_EXHAUSTED: '주간 AI 예산이 소진되어 대기 중입니다. 관리자가 설정에서 한도를 늘리거나 다음 주 예산이 적용되면 자동으로 재개됩니다.',
  AI_LEGACY_CONFIG_REQUIRED: '이전 작업의 AI 설정을 확인할 수 없습니다. 전사만 완료 또는 취소한 뒤, 자료를 열어 현재 AI로 새 생성을 요청하세요. 일반 재시도는 기존 설정을 유지합니다.',
  AI_GENERATION_CONFIG_CHANGED: '등록 당시 생성 설정과 현재 구현이 다릅니다. 전사만 완료 또는 취소한 뒤 현재 AI로 새 생성을 요청하세요.',
  AI_AUTH_REQUIRED: '이 작업에 등록된 AI의 인증이 필요합니다. 소유자가 설정에서 연결을 준비한 뒤 재시도하세요.',
  AI_AUTH_INVALID: 'AI 인증을 확인하지 못했습니다. 소유자가 인증을 갱신한 뒤 재시도하세요.',
  AI_CREDENTIALS_CHANGED: 'AI 키가 변경되어 생성을 중단했습니다. 연결을 확인한 뒤 재시도하세요.',
  AI_INSTALL_REQUIRED: '이 작업에 필요한 AI 실행 도구가 설치되지 않았습니다. 설정에서 준비 상태를 확인하세요.',
  AI_SUBSCRIPTION_REQUIRED: '전용 런타임의 공식 구독 로그인이 필요합니다.',
  AI_PROVIDER_BUSY: '동일한 AI가 다른 생성을 처리하고 있습니다. 잠시 후 재시도하세요.',
  AI_SERVICE_UNAVAILABLE: 'AI 서비스가 일시적으로 요청을 처리하지 못했습니다. 잠시 후 재시도하세요.',
  AI_SERVER_ERROR: 'AI 공급자 서버에서 오류가 발생했습니다. 잠시 후 재시도하세요.',
  AI_NETWORK: 'AI 서비스에 연결하지 못했습니다. 네트워크를 확인한 뒤 재시도하세요.',
  AI_RATE_LIMIT: 'AI 서비스 요청 한도에 도달했습니다. 잠시 후 재시도하세요.',
  AI_MODEL_METADATA_REQUIRED: '등록 당시 모델 한도 정보가 없었습니다. 연결을 준비한 뒤 전사만 완료 또는 취소하고 새 생성을 요청하세요.',
  AI_MODEL_UNAVAILABLE: '등록된 AI 모델을 사용할 수 없습니다. 설정 확인 후 필요하면 전사만 완료하고 새 생성을 요청하세요.',
  YOUTUBE_URL_INVALID: '지원하는 YouTube 단일 영상 링크를 입력하세요.',
  YOUTUBE_NOT_SINGLE_VIDEO: '단일 영상만 처리할 수 있습니다.',
  YOUTUBE_LIVE_UNSUPPORTED: '진행 중이거나 아직 준비되지 않은 라이브 영상은 처리할 수 없습니다.',
  YOUTUBE_ACCESS_RESTRICTED: '공개·일부 공개 영상만 지원하며 로그인·유료 접근·DRM 자료는 처리할 수 없습니다.',
  YOUTUBE_ACCESS_FAILED: 'YouTube 영상에 접근하지 못했습니다. 공개 상태나 네트워크를 확인하세요.',
  YOUTUBE_DURATION_UNKNOWN: '영상 길이를 확인하지 못해 처리를 중단했습니다.',
  YOUTUBE_AUDIO_UNAVAILABLE: '처리할 수 있는 음성 트랙이 없습니다.',
  YOUTUBE_AUDIO_FORMAT_UNSUPPORTED: '이 영상의 음성 형식은 링크 입력으로 처리할 수 없습니다.',
  YOUTUBE_RUNTIME_REQUIRED: '영상 처리 도구가 준비되지 않았습니다. 설정·진단에서 설치 상태를 확인하세요.',
  YOUTUBE_PROCESS_START_FAILED: '영상 처리 프로세스를 시작하지 못했습니다. 설정·진단에서 실행 환경을 확인하세요.',
  YOUTUBE_TIMEOUT: '영상 획득 제한 시간을 초과해 중단했습니다.',
  YOUTUBE_TRANSFER_TIMEOUT: '영상 획득 제한 시간을 초과해 중단했습니다.',
  YOUTUBE_DOWNLOAD_INCOMPLETE: '영상 전체 음성을 받지 못해 처리를 중단했습니다.',
  FILE_SIZE_LIMIT: '입력 크기가 2GB 제한을 초과했습니다.',
  DURATION_LIMIT: '입력 길이가 120분 제한을 초과했습니다.',
}

export function jobError(code: string, youtube = false): string {
  const message = messages[code] || (code.startsWith('YOUTUBE_') ? '영상 획득에 실패해 처리를 중단했습니다.' : code)
  return message + (youtube || code.startsWith('YOUTUBE_') ? ' 허가된 파일을 직접 업로드할 수 있습니다.' : '')
}
