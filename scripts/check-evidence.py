"""Validate recorded costly executions without silently repeating them."""
import argparse
import hashlib
import json
from pathlib import Path


REQUIRED = {'model-ko', 'model-en', 'live-ko', 'live-en', 'live-long', 'installation'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inside(root, name):
    path = root / name
    if Path(name).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Evidence path escapes its root')
    return path


def audit(root, manifest_path, include_soak=False):
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported evidence schema')
    checks = manifest['checks']
    ids = [check['id'] for check in checks]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate evidence ID')
    required = REQUIRED | ({'soak'} if include_soak else set())
    findings = [{'id': key, 'reason': 'MISSING'} for key in sorted(required - set(ids))]
    for check in checks:
        if check['id'] == 'soak' and not include_soak:
            continue
        reasons = []
        if check['status'] != 'PASS':
            reasons.append('NOT_PASS')
        evidence = inside(manifest_path.parent, check['path'])
        if not evidence.is_file() or digest(evidence) != check['sha256']:
            reasons.append('EVIDENCE_CHANGED_OR_MISSING')
        if not check.get('inputs'):
            reasons.append('NO_INPUT_FINGERPRINTS')
        changed = []
        for name, expected in check.get('inputs', {}).items():
            source = inside(root, name)
            if not source.is_file() or digest(source) != expected:
                changed.append(name)
        if changed:
            reasons.append('STALE_INPUTS')
        if reasons:
            findings.append({'id': check['id'], 'reasons': reasons, 'changed_inputs': changed})
    return {'status': 'PASS' if not findings else 'NOT_VERIFIED', 'findings': findings,
            'execution': 'evidence integrity/freshness only; no inference or subscription calls',
            'scope': 'Recorded required executions; semantic coverage and review gates require release audit'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, default=Path('.workflow/evidence/execution-index.json'))
    parser.add_argument('--include-soak', action='store_true', help='Audit optional historical soak evidence; never executes it')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        result = audit(root, args.manifest.resolve(), args.include_soak)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'status': 'NOT_VERIFIED', 'reason': type(error).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 2)


if __name__ == '__main__':
    main()
