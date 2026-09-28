"""명시적인 공개 파일 목록으로 로컬 릴리즈를 구성한다."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile


SCRIPTS = ('install-ubuntu.sh', 'setup-toolchain.py', 'setup-youtube.py', 'run.sh', 'stop.sh', 'backup.sh', 'restore.sh',
           'doctor.sh', 'setup-claude.py', 'login-claude.py', 'import-gemini-key.py', 'login-codex.sh', 'prepare-models.sh', 'install-service.sh', 'dev-windows.ps1',
           'check.sh', 'sync-source.py', 'check-evidence.py', 'check-costly.py', 'check-line-endings.py', 'build-release.py', 'verify_dispatch.py', 'consume-review-acks.py')
REQUIRED = ('README.md', 'AGENTS.md', '.gitattributes', '.editorconfig', 'docs/milestone.md', 'docs/workflow.md',
            'pyproject.toml', 'uv.lock', 'apps/web/package.json', 'apps/web/package-lock.json', 'apps/web/dist/index.html',
            'notices/THIRD_PARTY.md', 'notices/PYTHON_DEPENDENCIES.json',
            'notices/react-LICENSE.txt', 'notices/react-dom-LICENSE.txt', 'notices/scheduler-LICENSE.txt')


def collect(root):
    files = set(REQUIRED) | {'scripts/' + name for name in SCRIPTS}
    for directory, suffixes in [('src', {'.py', '.json'}), ('notices', {'.txt', '.md'}),
                                ('apps/web/src', {'.ts', '.tsx', '.css'}), ('apps/web/dist', None),
                                ('tests', {'.py', '.json'}), ('apps/web/e2e', {'.ts'})]:
        files.update(str(path.relative_to(root)) for path in (root / directory).rglob('*')
                     if path.is_file() and '__pycache__' not in path.parts and (suffixes is None or path.suffix in suffixes))
    files.update('apps/web/' + name for name in
                 ('index.html', 'playwright.config.ts', 'tsconfig.json', 'vite.config.ts'))
    for name in files:
        path = root / name
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root.parent) or not path.is_file() or path.resolve().is_relative_to(root.resolve()) is False:
            raise ValueError('Invalid release file: ' + name)
        if any(part in {'.workflow', '.git', '.codex', '.apikey', '.claude', 'claude-home', 'claude-user-home', 'codex-home', 'runtime-user-home', 'auth.json', '.credentials.json', 'owner-key', 'gemini.json', '.gemini_api_key', '.claude.json', 'data', 'node_modules', '.venv'} for part in path.relative_to(root).parts):
            raise ValueError('Private release path')
        if any(part.startswith(('.gemini-', '.claude.json.')) for part in path.relative_to(root).parts):
            raise ValueError('Private release temporary path')
    return sorted(files)


def revision_payloads(root, files, commit):
    # Use canonical Git bytes, independent of checkout newline conversion.
    return {name: (root / name).read_bytes() if name.startswith('apps/web/dist/') else
            subprocess.check_output(['git', 'show', commit + ':' + name], cwd=root)
            for name in files}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('dist/local-meeting-minutes-0.1.0-ubuntu24.04-x86_64.tar.gz'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = collect(root)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip())
    if dirty:
        raise RuntimeError('Commit release sources before building a pinned bundle')
    payloads = revision_payloads(root, files, commit)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
    payloads['RELEASE_MANIFEST.json'] = json.dumps({'version': '0.1.0', 'source_commit': commit, 'source_dirty': dirty,
        'verification': 'PREVIEW / NOT_FULLY_VERIFIED', 'files': hashes}, ensure_ascii=False, indent=2).encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, filename='') as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as archive:
            for name, data in sorted(payloads.items()):
                entry = tarfile.TarInfo('local-meeting-minutes/' + name)
                entry.size = len(data)
                entry.mode = 0o755 if name.endswith('.sh') else 0o644
                archive.addfile(entry, io.BytesIO(data))
    checksum = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(args.output.suffix + '.sha256').write_text(checksum + '  ' + args.output.name + '\n')
    print(json.dumps({'path': str(args.output.resolve()), 'sha256': checksum, 'files': len(payloads), 'source_dirty': dirty}))


if __name__ == '__main__':
    main()
