import hashlib
import json

from .ai_common import INSTRUCTIONS
from .minutes_generation import generation_model, prompt_version
from .utterances import RULE_VERSION
from .minutes_context import FORMAT_VERSION, TASKS


def generation_identity(version, meeting, settings, ai_config=None):
    data = {'version': version, 'model': settings.codex_model, 'effort': 'medium', 'prompt': INSTRUCTIONS, 'tasks': TASKS,
            'format_version': FORMAT_VERSION,
            'input_bytes': settings.codex_input_bytes, 'integration_layout': 'references',
            'schema': generation_model(meeting).model_json_schema(), 'prompt_version': prompt_version(meeting),
            'source_kind': meeting.get('source_kind', 'file'), 'source_metadata': meeting.get('source_metadata', {}),
            'document_kind': meeting.get('document_kind', 'meeting'), 'utterance_rule': RULE_VERSION,
            'meeting': {key: meeting.get(key) for key in ('title', 'occurred_at', 'timezone')}}
    if ai_config is not None:
        data.pop('model')
        data.pop('effort')
        data.pop('input_bytes')
        data['ai_config'] = ai_config.model_dump(mode='json')
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
