"""등록된 기존 검증 세션으로 범위가 명시된 요청을 한 번 전송한다."""
import argparse
import hashlib
import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


def validate_scope(packet):
    gate = packet.get('gate', 'PREFLIGHT')
    if gate.startswith('PREFLIGHT'):
        if any(packet[key] for key in ('allow_full_suite', 'allow_model_run', 'allow_live_codex')):
            raise ValueError('preflight scope cannot run full suite/models/LLM')
        return
    if gate not in {'R1', 'R2', 'R3'} or not re.fullmatch(r'[0-9a-f]{40}', packet['head_commit']):
        raise ValueError('invalid review gate/commit')
    if any(type(packet[key]) is not bool for key in ('allow_full_suite', 'allow_model_run', 'allow_live_codex')):
        raise ValueError('scope flags must be explicit booleans')
    if packet['allow_full_suite'] and gate != 'R3':
        raise ValueError('full suite requires explicit R3 request')
    budget = packet.get('execution_budget', {})
    for flag, limit in [('allow_model_run', 'max_model_runs'), ('allow_live_codex', 'max_codex_calls')]:
        if packet[flag] and (type(budget.get(limit)) is not int or budget[limit] <= 0):
            raise ValueError('costly runs require explicit positive execution budget')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registry', required=True, type=Path)
    parser.add_argument('--request-id', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', args.request_id):
        parser.error('invalid request ID')
    registry_path = args.registry.resolve(strict=True)
    registry = json.loads(registry_path.read_text(encoding='utf-8-sig'))
    master = str(uuid.UUID(registry['master']['thread_id']))
    verifier = str(uuid.UUID(registry['verifier']['thread_id']))
    if master == verifier:
        parser.error('master/verifier must differ')
    root = Path(registry['shared_workflow_root']).resolve(strict=True)
    if registry_path.parent != root:
        parser.error('registry must belong to shared workflow root')
    request = root / 'reviews' / args.request_id / 'request.json'
    packet = json.loads(request.read_text(encoding='utf-8-sig'))
    if packet['request_id'] != args.request_id or packet['reply_to_thread_id'] != master:
        parser.error('request identity mismatch')
    try:
        validate_scope(packet)
    except ValueError as exc:
        parser.error(str(exc))
    receipt = request.with_name('dispatch.json')
    reservation = {"status": "DISPATCH_RESERVED", "request_id": args.request_id,
                   "checked_at": datetime.now(timezone.utc).isoformat(),
                   "packet_sha256": hashlib.sha256(request.read_bytes()).hexdigest()}
    try:
        with receipt.open('x', encoding='utf-8') as f:
            json.dump(reservation, f, indent=2)
    except FileExistsError:
        print('DUPLICATE_SUPPRESSED: inspect existing dispatch/result; no automatic retry')
        return
    message = f'VERIFY_REQUEST id={args.request_id}; commit={packet["head_commit"]}; packet={request}; 지정 범위만 검증하고 최종 result.json 저장 후 등록된 마스터에게 VERIFY_RESULT로 한 번 회신하라. 중복 요청은 무회신 재사용하고 결과 파일은 회신 후 수정하지 않는다.'
    try:
        proc = subprocess.run([registry['queue_cli'], 'queue', '--thread', verifier, '--message', message],
                              capture_output=True, text=True, timeout=45)
        reservation.update(status='QUEUED_UNACKNOWLEDGED' if proc.returncode == 0 else 'DISPATCH_FAILED',
                           exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        reservation.update(status='DISPATCH_UNCERTAIN', error=type(exc).__name__)
    temp = receipt.with_suffix('.tmp')
    temp.write_text(json.dumps(reservation, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(receipt)
    print(json.dumps(reservation, ensure_ascii=False))
    if reservation['status'] != 'QUEUED_UNACKNOWLEDGED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
