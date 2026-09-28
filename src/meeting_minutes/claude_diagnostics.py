"""Local Claude runtime metadata only; never return authentication output."""
import json
from pathlib import Path
import re
import tempfile
import time
from .ai_common import AIFailure
from .claude_provider import VERSION, environment, isolation
from .cli_process import bounded_cli


def runtime_status(settings):
    result = {'path':str(settings.claude_cli), 'home':str(settings.claude_home),
              'version':None, 'login':'unknown', 'error':None, 'checked_at':time.time()}
    if not settings.claude_cli.is_file():
        return result | {'error':'AI_INSTALL_REQUIRED'}
    try:
        isolation(settings)
        with tempfile.TemporaryDirectory(prefix='minutes-claude-diagnostics-') as folder:
            cwd = Path(folder)
            env = environment(settings,temporary=cwd)
            code, output, _ = bounded_cli([str(settings.claude_cli),'--version'],cwd=cwd,env=env,timeout=5,output_limit=8192,error_prefix='AI')
            match = re.fullmatch(rb'([0-9][0-9A-Za-z.+-]{0,60}) \(Claude Code\)\s*',output)
            if code or not match:
                return result | {'error':'AI_CLI_VERSION_UNVERIFIED'}
            result['version'] = match.group(1).decode('ascii')
            if result['version'] != VERSION:
                result['error'] = 'AI_CLI_VERSION_UNVERIFIED'
            code, output, _ = bounded_cli([str(settings.claude_cli),'auth','status'],cwd=cwd,env=env,timeout=5,output_limit=8192,error_prefix='AI')
            auth = json.loads(output)
            if not isinstance(auth,dict):
                return result | {'error':'AI_AUTH_STATUS_UNVERIFIED'}
            if not code and auth.get('loggedIn') is True:
                result['login'] = 'claude_subscription' if auth.get('authMethod') == 'claude.ai' and auth.get('apiProvider') in (None,'firstParty') else 'unsupported'
            else:
                result['login'] = 'not_logged_in'
    except AIFailure as exc:
        result['error'] = exc.code
        if exc.code == 'AI_AUTH_REQUIRED':
            result['login'] = 'not_logged_in'
    except (ValueError,UnicodeError):
        result['error'] = 'AI_AUTH_STATUS_UNVERIFIED'
    except OSError:
        result['error'] = 'AI_EXEC_UNAVAILABLE'
    return result
