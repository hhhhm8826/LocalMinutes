"""Gemini errors reduced to fixed codes and allowlisted non-content diagnostics."""
import math
import re
import httpx
from .ai_common import AIFailure


def failure(error):
    code = getattr(error, 'code', None)
    diagnostic = {}
    if type(code) is int and 100 <= code <= 599:
        diagnostic.update(category='http_api', http_status=code,
                          provider_status=getattr(error, 'status', None))
        response = getattr(error, 'response', None)
        headers = getattr(response, 'headers', {})
        delay = headers.get('retry-after', '')
        if re.fullmatch(r'[0-9]{1,5}', delay):
            diagnostic['retry_after_seconds'] = int(delay)
        details = getattr(error, 'details', {})
        details = details.get('error', details) if isinstance(details, dict) else {}
        items = details.get('details', []) if isinstance(details, dict) else []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get('@type') == 'type.googleapis.com/google.rpc.RetryInfo':
                duration = item.get('retryDelay', '')
                if isinstance(duration, str) and re.fullmatch(r'[0-9]{1,5}(\.[0-9]{1,9})?s', duration):
                    diagnostic['retry_after_seconds'] = math.ceil(float(duration[:-1]))
        message = str(getattr(error, 'message', '')).lower()
        if code == 400:
            result = ('AI_AUTH_INVALID' if 'api key' in message or 'api_key_invalid' in message else
                      'AI_SCHEMA_UNSUPPORTED' if 'schema' in message else
                      'AI_REGION_UNSUPPORTED' if 'location' in message or 'region' in message else 'AI_BAD_REQUEST')
        else:
            result = {401:'AI_AUTH_INVALID', 403:'AI_AUTH_INVALID', 404:'AI_MODEL_UNAVAILABLE',
                      429:'AI_RATE_LIMIT', 503:'AI_SERVICE_UNAVAILABLE'}.get(code,
                      'AI_SERVER_ERROR' if code >= 500 else 'AI_REQUEST_REJECTED')
    else:
        kinds = ((httpx.ConnectTimeout,'connect_timeout'), (httpx.ReadTimeout,'read_timeout'),
                 (httpx.WriteTimeout,'write_timeout'), (httpx.PoolTimeout,'pool_timeout'),
                 (httpx.TimeoutException,'timeout'), (httpx.ConnectError,'connect_error'),
                 (httpx.ReadError,'read_error'), (httpx.WriteError,'write_error'),
                 (httpx.ProtocolError,'protocol_error'))
        diagnostic['category'] = next((kind for cls,kind in kinds if isinstance(error,cls)), 'transport_error')
        result = 'AI_TIMEOUT' if isinstance(error,httpx.TimeoutException) else 'AI_NETWORK'
    return AIFailure(result, diagnostic)
