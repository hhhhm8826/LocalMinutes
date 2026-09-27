const messages: Record<string, string> = {
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
