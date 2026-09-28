"""Subscription-only CLI boundary. No transcript in argv, environment or logs."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time

import jsonschema
from .temporary import ROOT
from .cli_process import bounded_cli as bounded_cli, kill_tree as kill_tree
from .ai_common import INSTRUCTIONS as INSTRUCTIONS, AIFailure as CodexFailure


DISABLED_FEATURES = ('shell_tool', 'apps', 'hooks', 'plugins', 'remote_plugin', 'browser_use',
    'browser_use_external', 'computer_use', 'image_generation', 'in_app_browser', 'multi_agent',
    'code_mode', 'code_mode_host', 'skill_search', 'skill_mcp_dependency_install', 'memories',
    'view_image', 'sleep_tool')
# Import aliases keep legacy callers and stored error contracts compatible.


def classify_error(value):
    value = value.lower()
    groups = (
        ('CODEX_LOGIN_REQUIRED', ('not logged in', 'unauthorized', 'authentication', '401', 'token_expired')),
        ('CODEX_USAGE_LIMIT', ('usage_limit', 'usage limit', 'rate_limit', 'rate limit', 'quota', '429')),
        ('CODEX_MODEL_UNAVAILABLE', ('model_not_found', 'model is not supported', 'does not have access', 'model access')),
        ('CODEX_NETWORK', ('connection', 'network', 'dns', 'stream disconnected', '502', '503', '504')),
    )
    return next((code for code, words in groups if any(word in value for word in words)), 'CODEX_FAILED')


def strict_schema(schema):
    """Codex structured outputs require every object property, including nullable ones."""
    if isinstance(schema, list):
        return [strict_schema(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    # Schema annotations may be removed, but identically named properties are data.
    result = {key: ({name: strict_schema(child) for name, child in value.items()}
                    if key in {'properties', '$defs', 'definitions', 'patternProperties'} else strict_schema(value))
              for key, value in schema.items() if key not in {'default', 'title'}}
    if result.get('type') == 'object':
        result['additionalProperties'] = False
        result['required'] = list(result.get('properties', {}))
    return result


def parse_events(raw):
    usage, kinds, errors, completed = {}, [], [], False
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise CodexFailure('CODEX_PROTOCOL_ERROR') from exc
        if not isinstance(event, dict):
            raise CodexFailure('CODEX_PROTOCOL_ERROR')
        item = event.get('item')
        if item:
            kind = item.get('type')
            kinds.append(kind)
            if kind not in {'agent_message', 'reasoning', 'error'}:
                raise CodexFailure('CODEX_UNEXPECTED_TOOL')
        if event.get('type') in {'turn.failed', 'error'}:
            errors.append(json.dumps(event))
        if event.get('type') == 'turn.completed':
            completed = True
            candidate = event.get('usage', {})
            if not isinstance(candidate, dict):
                raise CodexFailure('CODEX_PROTOCOL_ERROR')
            usage = {key: value for key, value in candidate.items()
                     if key in {'input_tokens', 'cached_input_tokens', 'output_tokens'}
                     and type(value) is int and value >= 0}
    return {'usage': usage, 'item_types': sorted(set(kinds)), 'error_codes': [classify_error(error) for error in errors],
            'completed': completed}


class CodexCliProvider:
    def __init__(self, settings):
        self.settings = settings
        self.temporary_prefix = 'localminutes-codex-'

    def environment(self):
        settings = self.settings
        if settings.codex_home == Path.home() / '.codex':
            raise CodexFailure('CODEX_RUNTIME_HOME_REQUIRED')
        for folder in (settings.codex_home, settings.codex_user_home):
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            folder.chmod(0o700)
        return {'PATH': '/usr/bin:/bin', 'HOME': str(settings.codex_user_home),
                'CODEX_HOME': str(settings.codex_home), 'LANG': 'C.UTF-8', 'NO_COLOR': '1'}

    def overrides(self):
        result = ['-c', 'forced_login_method="chatgpt"', '-c', 'web_search="disabled"',
                  '-c', 'project_doc_max_bytes=0', '-c', 'model_reasoning_effort="medium"',
                  '-c', 'analytics.enabled=false', '-c', 'otel.exporter="none"', '-c', 'otel.trace_exporter="none"',
                  '-c', 'developer_instructions=' + json.dumps(INSTRUCTIONS)]
        for feature in DISABLED_FEATURES:
            result.extend(['--disable', feature])
        return result

    def preflight(self, cwd, env):
        cli = str(self.settings.codex_cli)
        if not self.settings.codex_cli.is_file():
            raise CodexFailure('CODEX_INSTALL_REQUIRED')
        code, output, _ = bounded_cli([cli, '--version'], cwd=cwd, env=env, timeout=15)
        if code or output.decode().strip() != 'codex-cli 0.157.1':
            raise CodexFailure('CODEX_VERSION_UNVERIFIED')
        code, output, errors = bounded_cli([cli, 'login', 'status'], cwd=cwd, env=env, timeout=15)
        if code or b'ChatGPT' not in output + errors:
            raise CodexFailure('CODEX_LOGIN_REQUIRED')
        code, output, _ = bounded_cli([cli, *self.overrides(), 'features', 'list'], cwd=cwd, env=env, timeout=15)
        if code or any(not re.search(r'^' + feature + r'\s+.*\bfalse\s*$', output.decode(), re.M)
                       for feature in DISABLED_FEATURES):
            raise CodexFailure('CODEX_ISOLATION_UNVERIFIED')

    def generate(self, payload, schema, reserve_call):
        from .minutes_context import context_budget, render_prompt
        budget = self.runtime.budget(schema) if hasattr(self, 'runtime') else context_budget(self.settings, strict_schema(schema))
        prompt = render_prompt(payload)
        if len(prompt.encode()) > budget['max_prompt_bytes']:
            raise CodexFailure('CODEX_INPUT_REQUIRES_CHUNKING')
        env = self.environment()
        with tempfile.TemporaryDirectory(prefix=self.temporary_prefix, dir=ROOT) as directory:
            cwd = Path(directory)
            if any((parent / 'AGENTS.md').exists() or (parent / 'AGENTS.override.md').exists()
                   for parent in (cwd, *cwd.parents)):
                raise CodexFailure('CODEX_INHERITED_INSTRUCTIONS')
            self.preflight(cwd, env)
            schema_path, result_path = cwd / 'schema.json', cwd / 'result.json'
            schema_path.write_text(json.dumps(strict_schema(schema)))
            argv = [str(self.settings.codex_cli), *self.overrides(), '--ask-for-approval', 'never', 'exec',
                    '--strict-config', '--ignore-user-config', '--ignore-rules', '--sandbox', 'read-only',
                    '--ephemeral', '--skip-git-repo-check', '--model', self.settings.codex_model,
                    '--output-schema', str(schema_path), '--output-last-message', str(result_path), '--json', '-']
            reserve_call()  # Durable reservation precedes any network-capable generation.
            started = time.monotonic()
            code, stdout, stderr = bounded_cli(argv, cwd=cwd, env=env, input_bytes=prompt.encode(),
                timeout=self.settings.codex_timeout_seconds, result_path=result_path)
            events = parse_events(stdout)
            if code or events['error_codes']:
                raise CodexFailure(events['error_codes'][0] if events['error_codes'] else classify_error(stderr.decode(errors='replace')))
            if events['completed'] and 'output_tokens' not in events['usage']:
                raise CodexFailure('CODEX_PROTOCOL_ERROR')
            if not events['completed']:
                raise CodexFailure('CODEX_INCOMPLETE_TURN')
            if not result_path.is_file() or result_path.is_symlink() or result_path.stat().st_size > 1_000_000:
                raise CodexFailure('CODEX_INVALID_JSON')
            try:
                value = json.loads(result_path.read_text())
            except (ValueError, UnicodeError) as exc:
                raise CodexFailure('CODEX_INVALID_JSON') from exc
            try:
                jsonschema.validate(value, strict_schema(schema))
            except jsonschema.ValidationError as exc:
                raise CodexFailure('MINUTES_SCHEMA_INVALID') from exc
            return value, {**events, 'wall_seconds': time.monotonic() - started, 'model': self.settings.codex_model,
                           'cli_version': '0.157.1', 'stderr_sha256': hashlib.sha256(stderr).hexdigest(),
                           'context_budget': budget, 'prompt_bytes': len(prompt.encode())}
