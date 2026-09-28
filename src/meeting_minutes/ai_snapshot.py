"""Immutable, credential-free configuration captured in the job transaction."""
import hashlib
import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import text

from .ai_capabilities import Capabilities, discover
from .ai_common import AIFailure, INSTRUCTIONS
from .minutes_context import FORMAT_VERSION, TASKS, MEETING_TOPICS
from .minutes_generation import generation_model, prompt_version

ADAPTER_VERSIONS = {'codex_cli': 2, 'gemini_api': 3, 'claude_cli': 2}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class AIConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    provider: Literal['codex_cli', 'gemini_api', 'claude_cli']
    model: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9._-]+$')
    policy_revision: int = Field(ge=0)
    adapter_version: int = Field(ge=1)
    prompt_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    schema_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    format_version: int = Field(ge=1)
    timeout_seconds: int = Field(ge=10, le=1800)
    input_bytes: int = Field(ge=1000, le=1000000)
    max_calls: int = Field(ge=1, le=30)
    capabilities: Capabilities | None

    @model_validator(mode='after')
    def matching_capabilities(self):
        if self.capabilities and (self.capabilities.provider != self.provider or self.capabilities.model != self.model):
            raise ValueError('capability identity mismatch')
        return self


def signatures(meeting):
    return {'prompt_hash': digest([INSTRUCTIONS, TASKS, MEETING_TOPICS, prompt_version(meeting)]),
            'schema_hash': digest(generation_model(meeting).model_json_schema()),
            'format_version': FORMAT_VERSION}


def capture(connection, settings, meeting):
    # The caller owns BEGIN IMMEDIATE: policy selection and job insertion are atomic.
    models = {'codex_cli': settings.codex_model, 'gemini_api': 'gemini-3.8-flash', 'claude_cli': 'claude-opus-5-5'}
    connection.execute(text('INSERT OR IGNORE INTO ai_policy VALUES (1,:provider,:models,1,:now)'),
        {'provider': 'codex_cli', 'models': json.dumps(models), 'now': time.time()})
    row = connection.execute(text('SELECT * FROM ai_policy WHERE id=1')).mappings().one()
    provider = row['active_provider']
    return configuration(settings, meeting, provider, json.loads(row['models_json'])[provider], row['revision'])


def configuration(settings, meeting, provider, model, revision=0):
    return AIConfig(provider=provider, model=model, capabilities=discover(settings, provider, model),
        policy_revision=revision, adapter_version=ADAPTER_VERSIONS[provider],
        timeout_seconds=settings.codex_timeout_seconds, input_bytes=settings.codex_input_bytes,
        max_calls=settings.codex_max_calls, **signatures(meeting))


def parse(raw, meeting):
    if not raw:
        # An old job without recoverable configuration must never silently use the new policy.
        raise AIFailure('AI_LEGACY_CONFIG_REQUIRED')
    try:
        value = AIConfig.model_validate_json(raw)
    except (ValidationError, ValueError, TypeError):
        raise AIFailure('AI_CONFIG_INVALID') from None
    if (value.adapter_version != ADAPTER_VERSIONS[value.provider]
            or any(getattr(value, key) != item for key, item in signatures(meeting).items())):
        raise AIFailure('AI_GENERATION_CONFIG_CHANGED')
    return value


def codex_settings(settings, config):
    # Transitional Codex bridge; never assign another provider's model to codex_model.
    if config.provider != 'codex_cli':
        raise AIFailure('AI_ADAPTER_NOT_READY')
    return settings.model_copy(update={'codex_model': config.model, 'codex_timeout_seconds': config.timeout_seconds,
        'codex_input_bytes': config.input_bytes, 'codex_max_calls': config.max_calls})
