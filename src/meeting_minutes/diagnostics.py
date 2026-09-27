"""모델을 로드하거나 생성하지 않는 로컬 진단. 인증 원문은 반환하지 않는다."""
import json
from pathlib import Path
import re
import shutil
import tempfile
import time

from sqlalchemy import text

from .codex_provider import CodexFailure, bounded_cli
from .speech import MODEL_REVISIONS


REQUIRED_FILES = {
    'asr': ('config.json', 'model.bin', 'tokenizer.json'),
    'ko_align': ('config.json', 'pytorch_model.bin', 'preprocessor_config.json'),
    'diarization': ('config.yaml', 'embedding/pytorch_model.bin', 'segmentation/pytorch_model.bin',
                    'plda/plda.npz', 'plda/xvec_transform.npz'),
}


def model_cache(settings):
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        root = Path(HF_HUB_CACHE)
    except ImportError:
        root = None
    result = []
    for name, (repo, revision) in MODEL_REVISIONS.items():
        folder = root / ('models--' + repo.replace('/', '--')) / 'snapshots' / revision if root else None
        missing = [name for name in REQUIRED_FILES[name] if folder is None or not (folder / name).is_file() or (folder / name).stat().st_size == 0]
        result.append({'name': name, 'revision': revision, 'files_present': not missing, 'missing_files': missing})
    english = settings.cache_dir / 'torch/hub/checkpoints/wav2vec2_fairseq_base_ls960_asr_ls960.pth'
    result.append({'name': 'en_align', 'revision': 'torchaudio WAV2VEC2_ASR_BASE_960H',
                   'files_present': english.is_file() and english.stat().st_size > 0,
                   'missing_files': [] if english.is_file() else [english.name]})
    return {'hub_path': str(root) if root else None, 'models': result, 'inference_checked': False}


def runtime_status(settings):
    result = {'path': str(settings.codex_cli), 'home': str(settings.codex_home), 'configured_model': settings.codex_model,
              'version': None, 'login': 'unknown', 'configured_model_in_local_catalog': None, 'error': None,
              'account_remaining_quota': None, 'checked_at': time.time()}
    if settings.codex_home.resolve() == (Path.home() / '.codex').resolve():
        return result | {'error': 'CODEX_RUNTIME_HOME_REQUIRED'}
    if not settings.codex_cli.is_file():
        return result | {'error': 'CODEX_INSTALL_REQUIRED'}
    if not settings.codex_home.is_dir() or not settings.codex_user_home.is_dir():
        return result | {'login': 'not_logged_in', 'error': 'CODEX_LOGIN_REQUIRED'}
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(settings.codex_user_home), 'CODEX_HOME': str(settings.codex_home),
           'LANG': 'C.UTF-8', 'NO_COLOR': '1'}
    try:
        with tempfile.TemporaryDirectory(prefix='minutes-diagnostics-') as folder:
            code, output, _ = bounded_cli([str(settings.codex_cli), '--version'], cwd=Path(folder), env=env, timeout=5, output_limit=8192)
            match = re.fullmatch(rb'codex-cli ([0-9][0-9A-Za-z.+-]{0,60})\s*', output)
            if code or not match:
                return result | {'error': 'CODEX_VERSION_UNVERIFIED'}
            result['version'] = match.group(1).decode('ascii')
            code, output, errors = bounded_cli([str(settings.codex_cli), 'login', 'status'], cwd=Path(folder), env=env, timeout=5, output_limit=8192)
            result['login'] = 'chatgpt' if code == 0 and b'Logged in using ChatGPT' in output + errors else 'not_logged_in'
            if result['version'] != '0.157.1':
                result['error'] = 'CODEX_VERSION_UNVERIFIED'
    except (CodexFailure, OSError) as exc:
        result['error'] = exc.code if isinstance(exc, CodexFailure) else 'CODEX_EXEC_UNAVAILABLE'
    catalog = settings.codex_home / 'models_cache.json'
    try:
        if catalog.stat().st_size <= 2_000_000:
            result['configured_model_in_local_catalog'] = any(item.get('slug') == settings.codex_model
                for item in json.loads(catalog.read_text())['models'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result


def app_usage(repository):
    result = {'reserved_calls': 0, 'completed_calls': 0, 'input_tokens': 0, 'cached_input_tokens': 0,
              'output_tokens': 0, 'last_observed_model': None, 'scope': 'retained_local_records'}
    with repository.engine.connect() as connection:
        rows = connection.execute(text("SELECT metrics_json FROM usage_records WHERE stage='SUMMARIZE' ORDER BY created_at"))
        for row in rows:
            value = json.loads(row[0])
            if not value.get('call_reserved'):
                continue
            result['reserved_calls'] += 1
            result['completed_calls'] += int(value.get('completed') is True)
            for key in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
                number = value.get('usage', {}).get(key)
                if type(number) is int and number >= 0:
                    result[key] += number
            if isinstance(value.get('model'), str):
                result['last_observed_model'] = value['model']
    return result


def storage_status(settings):
    disk = shutil.disk_usage(settings.data_dir)
    return {'data_dir': str(settings.data_dir), 'cache_dir': str(settings.cache_dir),
            'config_dir': str(settings.config_dir), 'disk_free_bytes': disk.free, 'disk_total_bytes': disk.total}
