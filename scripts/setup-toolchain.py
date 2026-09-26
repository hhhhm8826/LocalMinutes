"""승인 후 Linux 사용자 영역에 고정 준비 도구를 설치한다. 전역 PATH는 변경하지 않는다."""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

NODE = '24.21.0'
UV = '0.12.19'
CODEX = '0.157.1'


def main():
    if platform.system() != 'Linux' or os.geteuid() == 0:
        raise SystemExit('Ubuntu 일반 사용자에서만 실행')
    root = Path.home() / '.local/share/local-meeting-minutes/toolchain'
    root.mkdir(parents=True, exist_ok=True)
    archive = root / f'node-v{NODE}-linux-x64.tar.xz'
    node_root = root / f'node-v{NODE}-linux-x64'
    base = f'https://nodejs.org/dist/v{NODE}/'
    checksums = urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=30).read().decode()
    expected = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1] == archive.name)
    if not archive.exists():
        partial = archive.with_suffix('.partial')
        urllib.request.urlretrieve(base + archive.name, partial)
        partial.replace(archive)
    actual = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
    if actual != expected:
        raise SystemExit('Node checksum mismatch; preserve file for diagnosis')
    if not node_root.exists():
        with tarfile.open(archive) as tar:
            if any(m.name != node_root.name and not m.name.startswith(node_root.name + '/') for m in tar.getmembers()):
                raise SystemExit('Unexpected archive root')
            tar.extractall(root, filter='data')
    uv_env = root / 'uv-env'
    if not (uv_env/'bin/python').exists():
        subprocess.run(['python3', '-m', 'venv', str(uv_env)], check=True)
    subprocess.run([str(uv_env/'bin/python'), '-m', 'pip', 'install', f'uv=={UV}'], check=True)
    env = dict(os.environ, PATH=f'{node_root}/bin:{uv_env}/bin:/usr/local/bin:/usr/bin:/bin')
    cli_root = root / 'codex'
    subprocess.run([str(node_root/'bin/npm'), 'install', '--prefix', str(cli_root), '--save-exact',
                    '--no-audit', '--no-fund', f'@openai/codex@{CODEX}'], env=env, check=True)
    env_file = root/'env.sh'
    env_file.write_text(f'export PATH="{cli_root}/node_modules/.bin:{node_root}/bin:{uv_env}/bin:/usr/local/bin:/usr/bin:/bin"\n')
    manifest = {'node': NODE, 'node_sha256': actual, 'uv': UV, 'codex_version': CODEX,
                'env_script': str(env_file), 'node_root': str(node_root),
                'codex': str(cli_root/'node_modules/.bin/codex'), 'uv_path': str(uv_env/'bin/uv')}
    (root/'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
