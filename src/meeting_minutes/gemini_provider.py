"""Official SDK adapter: fixed HTTPS origin, text-only input, no implicit auth/tools."""
from contextlib import contextmanager
import json
from importlib.metadata import version
import re
import ssl

import certifi
import time

import httpx
import jsonschema
from google import genai
from google.genai import errors, types

from .ai_common import AIFailure, INSTRUCTIONS
from .ai_secrets import GeminiSecretStore, SecretStoreError
from .minutes_context import render_prompt
from .gemini_schema import schema_instruction
from .gemini_failure import failure

ORIGIN = 'https://generativelanguage.googleapis.com'
SDK_VERSION = '2.25.0'
RESPONSE_LIMIT = 2_000_000


class FixedGoogleTransport(httpx.BaseTransport):
    def __init__(self, timeout):
        self.timeout = timeout
        self.inner = httpx.HTTPTransport(verify=True, trust_env=False, retries=0)

    def handle_request(self, request):
        if (request.url.scheme != 'https' or request.url.host != 'generativelanguage.googleapis.com'
                or request.url.port not in (None, 443) or request.url.query):
            raise AIFailure('AI_ENDPOINT_REJECTED')
        started = time.monotonic()
        response = self.inner.handle_request(request)
        body = bytearray()
        try:
            if 300 <= response.status_code < 400:
                raise AIFailure('AI_ENDPOINT_REJECTED')
            for part in response.iter_bytes():
                body.extend(part)
                if len(body) > RESPONSE_LIMIT:
                    raise AIFailure('AI_OUTPUT_LIMIT')
                if time.monotonic() - started > self.timeout:
                    raise AIFailure('AI_TIMEOUT')
            headers = {key: value for key, value in response.headers.items() if key.lower() not in {'content-encoding', 'content-length'}}
            return httpx.Response(response.status_code, headers=headers, content=bytes(body), request=request)
        finally:
            response.close()

    def close(self):
        self.inner.close()


def classify_error(error):
    return failure(error).code


class GeminiProvider:
    def __init__(self, runtime):
        if version('google-genai') != SDK_VERSION:
            raise AIFailure('AI_SDK_VERSION_UNVERIFIED')
        self.runtime = runtime
        self.store = GeminiSecretStore(runtime.settings)
        try:
            self.credential = self.store.read()
        except SecretStoreError as exc:
            raise AIFailure(exc.code) from None
        if self.credential is None:
            raise AIFailure('AI_AUTH_REQUIRED')
        self.actual_model = None

    def check_credential(self):
        try:
            current = self.store.status()
        except SecretStoreError as exc:
            raise AIFailure(exc.code) from None
        if not current['registered'] or current['credential_revision'] != self.credential.revision:
            raise AIFailure('AI_CREDENTIALS_CHANGED')

    @contextmanager
    def client(self):
        # Explicit false/URL/auth defeat cloud, proxy and endpoint environment fallback.
        transport = FixedGoogleTransport(self.runtime.timeout_seconds)
        with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                          timeout=self.runtime.timeout_seconds) as http:
            client = genai.Client(enterprise=False, vertexai=False, api_key=self.credential.key.get_secret_value(),
                http_options=types.HttpOptions(base_url=ORIGIN, api_version='v1beta',
                    timeout=self.runtime.timeout_seconds * 1000,
                    retry_options=types.HttpRetryOptions(attempts=1), httpx_client=http,
                    async_client_args={'trust_env': False, 'follow_redirects': False,
                        'verify': ssl.create_default_context(cafile=certifi.where()),
                        'ssl': ssl.create_default_context(cafile=certifi.where())}))
            try:
                yield client
            finally:
                client.close()

    def check(self):
        self.check_credential()
        self.runtime.budget({})  # Reject models without the required known capabilities.
        try:
            with self.client() as client:
                model = client.models.get(model=self.runtime.model)
        except (errors.APIError, httpx.HTTPError) as exc:
            raise failure(exc) from None
        except (ValueError, TypeError):
            raise AIFailure('AI_PROTOCOL_ERROR') from None
        if 'generateContent' not in (model.supported_actions or []):
            raise AIFailure('AI_MODEL_UNAVAILABLE')
        self.check_credential()
        return {'ready': True, 'code': None}

    def generate(self, payload, schema, reserve_call):
        budget = self.runtime.budget(schema)
        prompt = render_prompt(payload)
        if len(prompt.encode()) > budget['max_prompt_bytes']:
            raise AIFailure('AI_INPUT_REQUIRES_CHUNKING')
        prompt = schema_instruction(schema) + prompt
        self.check_credential()
        started = time.monotonic()
        try:
            with self.client() as client:
                reserve_call()
                response = client.models.generate_content(model=self.runtime.model, contents=prompt,
                    config=types.GenerateContentConfig(system_instruction=INSTRUCTIONS,
                        response_mime_type='application/json',
                        max_output_tokens=budget['output_reserve_tokens'], candidate_count=1,
                        tools=[], automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
        except (errors.APIError, httpx.HTTPError) as exc:
            raise failure(exc) from None
        except (ValueError, TypeError):
            raise AIFailure('AI_PROTOCOL_ERROR') from None
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            raise AIFailure('AI_SAFETY_BLOCKED')
        if not response.candidates:
            raise AIFailure('AI_EMPTY_OUTPUT')
        if len(response.candidates) != 1:
            raise AIFailure('AI_INCOMPLETE_OUTPUT')
        candidate = response.candidates[0]
        finish = candidate.finish_reason
        if finish in ('SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII'):
            raise AIFailure('AI_SAFETY_BLOCKED')
        if finish != 'STOP':
            raise AIFailure('AI_INCOMPLETE_OUTPUT')
        parts = candidate.content.parts if candidate.content else []
        content = []
        for part in parts or []:
            if any(getattr(part, key, None) is not None for key in
                   ('function_call', 'function_response', 'executable_code', 'code_execution_result', 'inline_data', 'file_data')):
                raise AIFailure('AI_UNEXPECTED_TOOL')
            if part.text and not part.thought:
                content.append(part.text)
        raw = ''.join(content)
        if not raw:
            raise AIFailure('AI_EMPTY_OUTPUT')
        if len(raw.encode()) > 1_000_000:
            raise AIFailure('AI_OUTPUT_LIMIT')
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise AIFailure('AI_INVALID_JSON') from None
        try:
            jsonschema.validate(value, schema)
        except jsonschema.ValidationError:
            raise AIFailure('MINUTES_SCHEMA_INVALID') from None
        actual = response.model_version
        self.actual_model = actual if isinstance(actual, str) and re.fullmatch(r'[a-zA-Z0-9._-]{1,100}', actual) else None
        usage = response.usage_metadata.model_dump() if response.usage_metadata else {}
        usage = {key: value for key, value in usage.items() if key.endswith('token_count') and type(value) is int and value >= 0}
        return value, {'usage': usage, 'completed': True, 'model': self.runtime.model,
            'actual_model': self.actual_model, 'sdk_version': SDK_VERSION,
            'wall_seconds': time.monotonic() - started, 'context_budget': budget, 'prompt_bytes': len(prompt.encode())}
