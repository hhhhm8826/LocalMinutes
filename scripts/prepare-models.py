"""CPU 사전작업 모델 캐시. 리비전 고정, 공유 flock, 결과는 PASS와 BLOCKED를 구분한다."""
import argparse
import gc
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.update(PYANNOTE_METRICS_ENABLED='0', HF_HUB_DISABLE_TELEMETRY='1',
                  DO_NOT_TRACK='1', OMP_NUM_THREADS='4', MKL_NUM_THREADS='4')


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    import fcntl
    parser = argparse.ArgumentParser()
    parser.add_argument('--include-gated', action='store_true')
    args = parser.parse_args()
    root = Path.home()/'.cache/local-meeting-minutes'
    root.mkdir(parents=True, exist_ok=True)
    lock = (root/'heavy.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.environ['HF_HOME'] = str(Path.home()/'.cache/huggingface')
    os.environ['TORCH_HOME'] = str(root/'torch')
    from huggingface_hub import HfApi, snapshot_download
    import torch
    import whisperx
    torch.set_num_threads(4)
    manifest_path = Path('.workflow/evidence/models-cache.json')
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'IN_PROGRESS',
                'telemetry': {'PYANNOTE_METRICS_ENABLED': '0', 'HF_HUB_DISABLE_TELEMETRY': '1'},
                'models': previous.get('models', {}), 'root': str(root)}

    def save():
        tmp = manifest_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        tmp.replace(manifest_path)

    repos = {'asr': 'mobiuslabsgmbh/faster-whisper-large-v3-turbo',
             'ko_align': 'kresnik/wav2vec2-large-xlsr-korean'}
    if args.include_gated:
        repos['diarization'] = 'pyannote/speaker-diarization-community-1'
    for key, repo in repos.items():
        start = time.monotonic()
        try:
            old = manifest['models'].get(key, {})
            revision = old.get('revision') or HfApi().model_info(repo).sha
            patterns = None if key == 'diarization' else ['*.json', '*.bin', '*.safetensors', '*.txt', '*.model', 'README.md']
            path = Path(snapshot_download(repo, revision=revision, allow_patterns=patterns))
            files = [{"path": str(p.relative_to(path)), "bytes": p.stat().st_size, "sha256": digest(p)}
                     for p in path.rglob('*') if p.is_file()]
            manifest['models'][key] = {'status': 'CACHED', 'repo_id': repo, 'revision': revision,
                'path': str(path), 'files': files, 'prepare_seconds': time.monotonic()-start}
        except Exception as exc:
            # Exception text can contain signed URLs; record type only.
            manifest['models'][key] = {'status': 'BLOCKED', 'repo_id': repo, 'error_type': type(exc).__name__}
        save()
        print(key, manifest['models'][key]['status'], flush=True)
    try:
        start = time.monotonic()
        align, metadata = whisperx.load_align_model('en', 'cpu', model_dir=str(root/'torch/hub/checkpoints'))
        manifest['models']['en_align'] = {'status': 'LOADED', 'name': 'WAV2VEC2_ASR_BASE_960H',
            'prepare_seconds': time.monotonic()-start,
            'files': [{'path': str(p), 'sha256': digest(p), 'bytes': p.stat().st_size}
                      for p in (root/'torch/hub/checkpoints').glob('*') if p.is_file()]}
        del align
        gc.collect()
    except Exception as exc:
        manifest['models']['en_align'] = {'status': 'BLOCKED', 'error_type': type(exc).__name__}
    vad = Path(whisperx.__file__).parent/'assets/pytorch_model.bin'
    manifest['models']['vad'] = {'status': 'CACHED', 'source': 'WhisperX 3.8.6 wheel asset',
                               'path': str(vad), 'sha256': digest(vad)}
    if not args.include_gated:
        manifest['models'].setdefault('diarization', {'status': 'BLOCKED', 'reason': 'Owner model terms/login pending'})
    manifest['status'] = 'CACHE_PARTIAL' if any(m['status'] == 'BLOCKED' for m in manifest['models'].values()) else 'CACHE_READY_NOT_INFERENCE_VERIFIED'
    save()
    print(manifest['status'])


if __name__ == '__main__':
    main()
