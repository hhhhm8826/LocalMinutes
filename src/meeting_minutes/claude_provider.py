"""Dedicated subscription Claude CLI; never inherit shell credentials or project tools."""
import json
import mmap
import os
from pathlib import Path
import stat
import tempfile
import time

import jsonschema

from .ai_common import AIFailure, INSTRUCTIONS
from .cli_process import bounded_cli
from .minutes_context import render_prompt
from .temporary import ROOT

VERSION = '2.1.283'
MANAGED_ROOT = Path('/etc/claude-code')
FLAGS = ('--safe-mode','--restricted','--tools','--disallowedTools','--strict-mcp-config',
         '--mcp-config','--permission-prompts','--no-session-persistence','--json-schema',
         '--setting-sources','--settings','--no-chrome','--effort')


# Documented controls, also required in the pinned native binary before authentication.
LIMIT_ENV = ('CLAUDE_CODE_MAX_RETRIES', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS',
    'CLAUDE_CODE_MAX_CONTEXT_TOKENS', 'API_TIMEOUT_MS', 'MAX_STRUCTURED_OUTPUT_RETRIES',
    'CLAUDE_CODE_DISABLE_TERMINAL_TITLE', 'FALLBACK_FOR_ALL_PRIMARY_MODELS')

def environment(settings, *, temporary=None):
    result = {'PATH':'/usr/bin:/bin','HOME':str(settings.claude_user_home),
        'CLAUDE_CONFIG_DIR':str(settings.claude_home),'LANG':'C.UTF-8','NO_COLOR':'1',
        'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','DISABLE_AUTOUPDATER':'1',
        'DISABLE_TELEMETRY':'1','DISABLE_ERROR_REPORTING':'1',
        'CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL':'1',
        'CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK':'1','CLAUDE_CODE_SKIP_PROMPT_HISTORY':'1',
        # This stops overload retries; it does not configure a fallback chain.
        # No fallback-model flag or settings source is enabled.
        'FALLBACK_FOR_ALL_PRIMARY_MODELS':'1','API_TIMEOUT_MS':'180000',
        'MAX_STRUCTURED_OUTPUT_RETRIES':'1','CLAUDE_CODE_DISABLE_TERMINAL_TITLE':'1',
        'CLAUDE_CODE_MAX_RETRIES':'0','CLAUDE_CODE_MAX_OUTPUT_TOKENS':'32000',
        'CLAUDE_CODE_MAX_CONTEXT_TOKENS':'200000'}
    if temporary:
        result.update(TMPDIR=str(temporary), CLAUDE_CODE_TMPDIR=str(temporary))
    return result


def isolation(settings):
    if settings.claude_home == settings.codex_home or settings.claude_user_home in (Path.home(), settings.codex_user_home):
        raise AIFailure('CLAUDE_RUNTIME_HOME_REQUIRED')
    for root in (settings.claude_home, settings.claude_user_home):
        if not root.is_dir():
            raise AIFailure('AI_AUTH_REQUIRED')
        info = root.stat()
        if root.is_symlink() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise AIFailure('AI_ISOLATION_UNVERIFIED')
    # Do not evade organization policy: refuse managed customization instead.
    if MANAGED_ROOT.exists() and any(MANAGED_ROOT.iterdir()):
        raise AIFailure('AI_ISOLATION_UNVERIFIED')
    for root in (settings.claude_home, settings.claude_user_home):
        if any(root.glob('**/managed-settings*')) or any(root.glob('**/managed-mcp*')):
            raise AIFailure('AI_ISOLATION_UNVERIFIED')


def classify(value):
    lower = value.lower()
    for code, patterns in (
        ('AI_AUTH_INVALID', ('not logged in','unauthorized','authentication','401','token_expired')),
        ('AI_RATE_LIMIT', ('rate_limit','rate limit','usage limit','quota','429')),
        ('AI_MODEL_UNAVAILABLE', ('model_not_found','model is not supported','model access','not available')),
        ('AI_NETWORK', ('network','connection','502','503','504'))):
        if any(pattern in lower for pattern in patterns):
            return code
    return 'AI_PROVIDER_ERROR'


class ClaudeProvider:
    def __init__(self, runtime):
        self.runtime, self.settings = runtime, runtime.settings
        self.temporary_prefix = 'localminutes-claude-'
        self.actual_model = None

    def preflight(self, cwd, env):
        if not self.settings.claude_cli.is_file():
            raise AIFailure('AI_INSTALL_REQUIRED')
        isolation(self.settings)
        cli = str(self.settings.claude_cli)
        code, output, _ = bounded_cli([cli,'--version'],cwd=cwd,env=env,timeout=10,error_prefix='AI')
        if code or output.decode(errors='replace').strip() != VERSION + ' (Claude Code)':
            raise AIFailure('AI_CLI_VERSION_UNVERIFIED')
        code, output, _ = bounded_cli([cli,'--help'],cwd=cwd,env=env,timeout=10,output_limit=100000,error_prefix='AI')
        if code or any(flag.encode() not in output for flag in FLAGS):
            raise AIFailure('AI_ISOLATION_UNVERIFIED')
        with self.settings.claude_cli.open('rb') as binary, mmap.mmap(binary.fileno(),0,access=mmap.ACCESS_READ) as data:
            signatures = (b'--max-turns <turns>', b'--system-prompt-file <file>',
                          *(name.encode() for name in LIMIT_ENV))
            if any(data.find(signature) < 0 for signature in signatures):
                raise AIFailure('AI_ISOLATION_UNVERIFIED')
        code, output, _ = bounded_cli([cli,'auth','status'],cwd=cwd,env=env,timeout=10,output_limit=32000,error_prefix='AI')
        try:
            status = json.loads(output)
        except (ValueError, UnicodeError):
            raise AIFailure('AI_AUTH_REQUIRED') from None
        if code or status.get('loggedIn') is not True or status.get('authMethod') != 'claude.ai':
            raise AIFailure('AI_SUBSCRIPTION_REQUIRED')
        if status.get('apiProvider') not in (None, 'firstParty'):
            raise AIFailure('AI_SUBSCRIPTION_REQUIRED')

    def check(self):
        self.runtime.budget({})
        with tempfile.TemporaryDirectory(prefix='minutes-claude-check-', dir=ROOT) as folder:
            cwd = Path(folder)
            self.preflight(cwd, environment(self.settings,temporary=cwd))
        return {'ready':True,'code':None,'checked_at':time.time()}

    def generate(self, payload, schema, reserve_call):
        self.actual_model = None
        budget = self.runtime.budget(schema)
        prompt = render_prompt(payload)
        if len(prompt.encode()) > budget['max_prompt_bytes']:
            raise AIFailure('AI_INPUT_REQUIRES_CHUNKING')
        with tempfile.TemporaryDirectory(prefix=self.temporary_prefix, dir=ROOT) as directory:
            cwd = Path(directory)
            env = environment(self.settings,temporary=cwd)
            env['API_TIMEOUT_MS'] = str(self.runtime.timeout_seconds * 1000)
            env['CLAUDE_CODE_MAX_OUTPUT_TOKENS'] = str(budget['output_reserve_tokens'])
            env['CLAUDE_CODE_MAX_CONTEXT_TOKENS'] = str(budget['effective_context_tokens'])
            self.preflight(cwd,env)
            system = cwd/'system.txt'
            system.write_text(INSTRUCTIONS)
            flags = ['--safe-mode','--restricted','--setting-sources','',
                '--settings',json.dumps({'disableAllHooks':True,'autoUpdatesChannel':'stable'}),
                '--tools','','--disallowedTools','mcp__*','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
                '--permission-mode','default','--permission-prompts','none','--no-chrome',
                '--no-session-persistence','--max-turns','1','--system-prompt-file',str(system),
                '--model',self.runtime.model,'--output-format','json','--json-schema',json.dumps(schema),'-p']
            if self.runtime.model == 'claude-opus-5-5':
                flags.extend(['--effort', 'high'])
            reserve_call()
            started = time.monotonic()
            code, stdout, stderr = bounded_cli([str(self.settings.claude_cli),*flags],cwd=cwd,env=env,
                input_bytes=prompt.encode(),timeout=self.runtime.timeout_seconds,output_limit=2_000_000,error_prefix='AI')
            if code:
                raise AIFailure(classify(stderr.decode(errors='replace') + stdout.decode(errors='replace')))
            try:
                envelope = json.loads(stdout)
            except (ValueError,UnicodeError):
                raise AIFailure('AI_INVALID_JSON') from None
            if not isinstance(envelope,dict) or envelope.get('type') != 'result':
                raise AIFailure('AI_PROTOCOL_ERROR')
            if envelope.get('is_error') or envelope.get('subtype') != 'success':
                raise AIFailure('AI_INCOMPLETE_OUTPUT')
            if envelope.get('permission_denials'):
                raise AIFailure('AI_UNEXPECTED_TOOL')
            value = envelope.get('structured_output')
            if value is None:
                raise AIFailure('AI_EMPTY_OUTPUT')
            try:
                jsonschema.validate(value,schema)
            except jsonschema.ValidationError:
                raise AIFailure('MINUTES_SCHEMA_INVALID') from None
            model_usage = envelope.get('modelUsage',{})
            names = list(model_usage) if isinstance(model_usage,dict) else []
            if names != [self.runtime.model]:
                raise AIFailure('AI_UNEXPECTED_MODEL')
            usage = envelope.get('usage', {})
            if (not isinstance(usage, dict) or type(usage.get('output_tokens')) is not int
                    or usage['output_tokens'] < 0):
                raise AIFailure('AI_PROTOCOL_ERROR')
            usage = {key:value for key,value in usage.items()
                     if key.endswith('_tokens') and type(value) is int and value >= 0}
            self.actual_model = names[0]
            return value, {'usage':usage,'completed':True,'cli_version':VERSION,'model':self.runtime.model,
                'actual_model':self.actual_model,'wall_seconds':time.monotonic()-started,
                'context_budget':budget,'prompt_bytes':len(prompt.encode())}
