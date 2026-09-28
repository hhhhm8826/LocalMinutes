"""Git 소스만 동기화하며 인증 경로는 추적 파일이어도 거부합니다."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from meeting_minutes.private_paths import private_path  # noqa: E402


def source_files(root):
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root).decode().split('\0')
    result = []
    for name in sorted(set(filter(None, names))):
        relative = Path(name)
        if private_path(name) or relative.is_absolute() or '..' in relative.parts or '.git' in relative.parts:
            raise ValueError('PRIVATE_SOURCE_PATH')
        path = root / relative
        if any(parent.is_symlink() for parent in [path, *path.parents]) or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('UNSAFE_SOURCE_PATH')
        if path.is_file():
            result.append(relative)
    return result


def sync(root, destination):
    root, destination = root.absolute(), destination.absolute()
    if root == destination or root.is_relative_to(destination) or destination.is_relative_to(root):
        raise ValueError('SEPARATE_SOURCE_DESTINATION_REQUIRED')
    files = source_files(root)  # Validate the complete list before publishing any file.
    for relative in files:
        target = destination / relative
        current = destination
        for part in relative.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise ValueError('UNSAFE_DESTINATION_PATH')
        if destination.is_symlink() or target.is_symlink():
            raise ValueError('UNSAFE_DESTINATION_PATH')
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.source-sync-', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write((root / relative).read_bytes())
            os.replace(temp, target)
        finally:
            Path(temp).unlink(missing_ok=True)
    return len(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print('Synced source files:', sync(Path(__file__).resolve().parents[1], args.destination))
