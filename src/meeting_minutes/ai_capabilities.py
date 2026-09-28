"""Capture non-secret model limits once; never discover them during a pinned job."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ai_common import AIFailure, INSTRUCTIONS


class Capabilities(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    provider: Literal['codex_cli', 'gemini_api', 'claude_cli']
    model: str
    context_tokens: int = Field(ge=65536, le=10000000)
    effective_context_tokens: int = Field(ge=1, le=10000000)
    output_tokens: int = Field(ge=1, le=1000000)
    overhead_tokens: int = Field(ge=0, le=1000000)
    source: Literal['codex-local-catalog-v1', 'gemini-3.5-flash-v1', 'gemini-3.8-flash-v1', 'claude-subscription-conservative-v1']

    @model_validator(mode='after')
    def valid_limits(self):
        if self.effective_context_tokens > self.context_tokens or self.output_tokens + self.overhead_tokens >= self.effective_context_tokens:
            raise ValueError('invalid model limits')
        return self


def discover(settings, provider, model):
    """None permits local transcription, but blocks later AI until a new request."""
    if provider == 'codex_cli':
        try:
            values = json.loads((settings.codex_home / 'models_cache.json').read_text())['models']
            item = next(value for value in values if value.get('slug') == model)
            window, percent = item['context_window'], item.get('effective_context_window_percent', 100)
            if type(window) is not int or type(percent) is not int or not 1 <= percent <= 100:
                return None
            return Capabilities(provider=provider, model=model, context_tokens=window,
                effective_context_tokens=window * percent // 100, output_tokens=65536,
                overhead_tokens=32768, source='codex-local-catalog-v1')
        except (OSError, ValueError, KeyError, TypeError, StopIteration, AttributeError):
            return None
    if provider == 'gemini_api' and model in ('gemini-3.5-flash', 'gemini-3.8-flash'):
        return Capabilities(provider=provider, model=model, context_tokens=1048576,
            effective_context_tokens=1048576, output_tokens=65536, overhead_tokens=32768, source=model+'-v1')
    if provider == 'claude_cli' and model in ('claude-sonnet-4-6', 'claude-opus-5-5'):
        return Capabilities(provider=provider, model=model, context_tokens=200000,
            effective_context_tokens=200000, output_tokens=32000, overhead_tokens=32768, source='claude-subscription-conservative-v1')
    return None


def budget(capabilities, schema, input_bytes, format_version):
    if capabilities is None:
        raise AIFailure('AI_MODEL_METADATA_REQUIRED')
    schema_bytes = len(json.dumps(schema, ensure_ascii=False).encode())
    instruction_bytes = len(INSTRUCTIONS.encode())
    limit = min(input_bytes, capabilities.effective_context_tokens - capabilities.output_tokens
                - capabilities.overhead_tokens - schema_bytes - instruction_bytes)
    if limit < 1000:
        raise AIFailure('AI_CONTEXT_BUDGET_TOO_SMALL')
    return {'model_context_tokens': capabilities.context_tokens,
        'effective_context_tokens': capabilities.effective_context_tokens,
        'output_reserve_tokens': capabilities.output_tokens, 'cli_reserve_tokens': capabilities.overhead_tokens,
        'schema_bytes': schema_bytes, 'instruction_bytes': instruction_bytes, 'max_prompt_bytes': limit,
        'format_version': format_version, 'count_method': 'conservative UTF-8 byte upper bound, not exact tokenization'}
