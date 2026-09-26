"""등록된 기존 검증 세션으로만 준비 요청을 전송한다. 실패도 자동 재전송하지 않는다."""
import argparse
import hashlib
import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


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
    if packet['allow_full_suite'] or packet['allow_model_run'] or packet['allow_live_codex']:
        parser.error('preflight scope cannot run full suite/models/LLM')
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
    message = f'PREFLIGHT_PING id={args.request_id}; registry={registry_path}; packet={request}; 지정 범위만 확인하고 결과 파일을 저장한 뒤 마스터 UUID로 codex queue 회신하라.'
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
