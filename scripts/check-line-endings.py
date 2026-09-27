"""Check tracked and non-ignored project text without modifying it."""
import argparse
import json
from pathlib import Path
import subprocess


def violations(data):
    if b'\0' in data:
        return None
    try:
        data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return None
    return data.count(b'\r')


def audit(root, index=False):
    args = ['git', 'ls-files', '-z', '--cached']
    if not index:
        args += ['--others', '--exclude-standard']
    names = sorted(set(subprocess.check_output(args, cwd=root).decode().split('\0')) - {''})
    findings = []
    checked = 0
    skipped = []
    for name in names:
        path = root / name
        if not index and (path.is_symlink() or not path.is_file()):
            skipped.append(name)
            continue
        data = subprocess.check_output(['git', 'show', ':' + name], cwd=root) if index else path.read_bytes()
        count = violations(data)
        if count is None:
            skipped.append(name)
            continue
        checked += 1
        if count:
            findings.append({'path': name, 'carriage_returns': count})
    return {'status': 'FAIL' if findings else 'PASS', 'index': index,
            'checked_text_files': checked, 'skipped_binary_or_missing': skipped, 'findings': findings}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--index', action='store_true', help='Check staged Git blobs instead of working files')
    args = parser.parse_args()
    report = audit(args.root, args.index)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(report['status'] != 'PASS')


if __name__ == '__main__':
    main()
