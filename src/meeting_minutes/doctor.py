"""설치 환경의 읽기 진단. 미준비 상태를 성공으로 표시하지 않는다."""
import json
import platform
import shutil

from .diagnostics import model_cache, runtime_status
from .settings import Settings


def main():
    settings = Settings()
    runtime, models = runtime_status(settings), model_cache(settings)
    checks = {'linux_x86_64': platform.system() == 'Linux' and platform.machine() == 'x86_64',
              'python_312': platform.python_version_tuple()[:2] == ('3', '12'),
              'ffmpeg': shutil.which('ffmpeg') is not None, 'ffprobe': shutil.which('ffprobe') is not None,
              'database_exists': settings.database_path.is_file(),
              'model_files_present': all(model['files_present'] for model in models['models']),
              'runtime_login': runtime['login'] == 'chatgpt', 'runtime_version': runtime['version'] == '0.157.1',
              'runtime_model_catalog': runtime['configured_model_in_local_catalog'] is True}
    ready = all(checks.values())
    print(json.dumps({'status': 'LOCAL_CHECKS_PASS' if ready else 'PREPARATION_REQUIRED', 'checks': checks,
                      'data_dir': str(settings.data_dir), 'runtime': runtime, 'models': models,
                      'inference_tested': False, 'remote_generation_tested': False}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if ready else 2)


if __name__ == '__main__':
    main()
