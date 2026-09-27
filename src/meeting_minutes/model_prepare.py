"""공개된 고정 모델 파일을 준비하고 검증한다. 모델 추론은 실행하지 않는다."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import urllib.request

from .settings import Settings


def verify(path, entry):
    if not path.is_file() or path.stat().st_size != entry['bytes']:
        raise ValueError('MODEL_FILE_SIZE_MISMATCH')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
            raise ValueError('MODEL_FILE_HASH_MISMATCH')


def prepare(settings, manifest, *, check_only, accept_terms):
    if not check_only and not accept_terms:
        raise ValueError('모델별 이용조건과 gated 모델의 연락처 공유를 확인한 뒤 --accept-model-terms를 지정하세요.')
    settings.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (settings.cache_dir / 'heavy.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from huggingface_hub import snapshot_download
        for model in manifest['models']:
            folder = Path(snapshot_download(model['repo_id'], revision=model['revision'],
                allow_patterns=[entry['path'] for entry in model['files']], local_files_only=check_only))
            for entry in model['files']:
                verify(folder / entry['path'], entry)
        english = manifest['english_alignment']
        destination = settings.cache_dir / 'torch/hub/checkpoints' / english['filename']
        if not destination.exists() and not check_only:
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            partial = destination.with_suffix('.download')
            try:
                with urllib.request.urlopen(english['url'], timeout=60) as incoming, partial.open('xb') as outgoing:
                    size = 0
                    while chunk := incoming.read(1024 * 1024):
                        size += len(chunk)
                        if size > english['bytes']:
                            raise ValueError('MODEL_DOWNLOAD_SIZE_EXCEEDED')
                        outgoing.write(chunk)
                verify(partial, english)
                partial.replace(destination)
            finally:
                partial.unlink(missing_ok=True)
        verify(destination, english)
    return {'status': 'CACHE_HASHES_VERIFIED', 'inference_tested': False, 'manifest_version': manifest['schema_version']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--accept-model-terms', action='store_true')
    args = parser.parse_args()
    os.environ.update(HF_HUB_DISABLE_TELEMETRY='1', PYANNOTE_METRICS_ENABLED='0', DO_NOT_TRACK='1')
    manifest = json.loads(Path(__file__).with_name('model_manifest.json').read_text(encoding='utf-8-sig'))
    try:
        print(json.dumps(prepare(Settings(), manifest, check_only=args.check_only, accept_terms=args.accept_model_terms)))
    except BlockingIOError:
        parser.exit(2, '모델 처리 또는 준비가 진행 중입니다. 완료 후 다시 실행하세요.\n')
    except Exception as exc:
        # 인증 오류에는 서명 URL이 포함될 수 있어 예외 원문을 출력하지 않는다.
        print(json.dumps({'status': 'MODEL_PREPARATION_REQUIRED', 'error_type': type(exc).__name__,
                          'terms': manifest['gated_terms_url'], 'hint': '모델 이용조건, hf auth login 및 파일 해시를 확인하세요.'}, ensure_ascii=False))
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
