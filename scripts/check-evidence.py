"""Validate recorded costly executions without silently repeating them."""
import argparse
import hashlib
import json
from pathlib import Path


REQUIRED = {'model-ko', 'model-en', 'live-ko', 'live-en', 'live-long', 'installation'}
REQUIRED_PROFILES = {
    'mvp': REQUIRED,
    'm3': {'secret-boundaries', 'provider-contracts', 'queue-snapshots', 'web-settings',
           'legacy-compatibility', 'operations', 'installation', 'review-m3-a', 'review-m3-b', 'regression',
           'live-codex-meeting', 'live-codex-video', 'live-gemini-meeting', 'live-gemini-video',
           'live-claude-meeting', 'live-claude-video'},
    'm2': {'model-ko', 'model-en', 'live-meeting', 'live-video',
           'semantic-comparison', 'youtube-acquisition', 'installation'},
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inside(root, name):
    path = root / name
    if Path(name).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Evidence path escapes its root')
    return path


def audit(root, manifest_path, include_soak=False, profile='mvp'):
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported evidence schema')
    if profile not in REQUIRED_PROFILES or manifest.get('profile', 'mvp') != profile:
        raise ValueError('Evidence profile mismatch')
    checks = manifest['checks']
    ids = [check['id'] for check in checks]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate evidence ID')
    required = REQUIRED_PROFILES[profile] | ({'soak'} if include_soak else set())
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
        elif profile == 'm3' and check['status'] == 'PASS':
            recorded = json.loads(evidence.read_text())
            if check['id'].startswith('review-'):
                if recorded.get('verdict') != 'PASS':
                    reasons.append('RECORDED_REVIEW_NOT_PASS')
                if not check.get('head_commit') or recorded.get('head_commit') != check['head_commit']:
                    reasons.append('REVIEW_COMMIT_MISMATCH')
            elif recorded.get('status') != 'PASS':
                reasons.append('RECORDED_RESULT_NOT_PASS')
            if check['id'].startswith('live-'):
                _, provider, kind = check['id'].split('-')
                expected_provider = {'codex':'codex_cli','gemini':'gemini_api','claude':'claude_cli'}[provider]
                expected_kind = 'meeting' if kind == 'meeting' else 'video_summary'
                if (recorded.get('execution_kind') != 'live' or recorded.get('provider') != expected_provider
                        or recorded.get('kind') != expected_kind or not recorded.get('document')
                        or not recorded.get('metrics', {}).get('completed')):
                    reasons.append('LIVE_EXECUTION_NOT_PROVEN')
        for name, expected in check.get('artifacts', {}).items():
            artifact = inside(manifest_path.parent, name)
            if not artifact.is_file() or digest(artifact) != expected:
                reasons.append('ARTIFACT_CHANGED_OR_MISSING')
                break
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
    parser.add_argument('--profile', choices=sorted(REQUIRED_PROFILES), default='mvp')
    parser.add_argument('--include-soak', action='store_true', help='Audit optional historical soak evidence; never executes it')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        result = audit(root, args.manifest.resolve(), args.include_soak, args.profile)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'status': 'NOT_VERIFIED', 'reason': type(error).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'PASS' else 2)


if __name__ == '__main__':
    main()
