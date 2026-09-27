import hashlib
import json

from .codex_provider import INSTRUCTIONS
from .contracts import Minutes
from .minutes_context import FORMAT_VERSION, TASKS


def generation_identity(version, meeting, settings):
    data = {'version': version, 'model': settings.codex_model, 'effort': 'medium', 'prompt': INSTRUCTIONS, 'tasks': TASKS,
            'format_version': FORMAT_VERSION,
            'input_bytes': settings.codex_input_bytes, 'integration_layout': 'references',
            'schema': Minutes.model_json_schema(),
            'meeting': {key: meeting.get(key) for key in ('title', 'occurred_at', 'timezone')}}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
