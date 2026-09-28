"""Provider registry, configured runtime and provider-specific context budgets."""

from .ai_common import AIFailure
from .ai_capabilities import budget as capability_budget
from .ai_snapshot import codex_settings
from .codex_provider import CodexCliProvider, strict_schema
from .minutes_context import FORMAT_VERSION, context_budget


class GenerationRuntime:
    def __init__(self, settings, config=None):
        self.settings, self.config = settings, config
        self.provider_id = config.provider if config else 'codex_cli'
        self.model = config.model if config else settings.codex_model
        self.max_calls = config.max_calls if config else settings.codex_max_calls
        self.input_bytes = config.input_bytes if config else settings.codex_input_bytes
        self.timeout_seconds = config.timeout_seconds if config else settings.codex_timeout_seconds

    def budget(self, schema):
        if self.config is None:
            return context_budget(self.settings, strict_schema(schema))
        capabilities = self.config.capabilities
        if capabilities and (capabilities.provider != self.provider_id or capabilities.model != self.model):
            raise AIFailure('AI_CONFIG_INVALID')
        result = capability_budget(capabilities, strict_schema(schema) if self.provider_id == 'codex_cli' else schema,
                                 self.input_bytes, FORMAT_VERSION)
        if self.provider_id == 'gemini_api':
            from .gemini_schema import schema_instruction
            result['max_prompt_bytes'] = min(result['max_prompt_bytes'], self.input_bytes - len(schema_instruction(schema).encode()))
            if result['max_prompt_bytes'] < 1000:
                raise AIFailure('AI_CONTEXT_BUDGET_TOO_SMALL')
        return result

    def identity(self):
        if self.config:
            return self.config.model_dump(mode='json')
        return {'provider': self.provider_id, 'model': self.model, 'input_bytes': self.input_bytes,
                'max_calls': self.max_calls, 'timeout_seconds': self.timeout_seconds}


def create_provider(runtime):
    if runtime.provider_id == 'codex_cli':
        settings = codex_settings(runtime.settings, runtime.config) if runtime.config else runtime.settings
        provider = CodexCliProvider(settings)
        provider.runtime = runtime
        return provider
    if runtime.provider_id == 'gemini_api':
        from .gemini_provider import GeminiProvider
        return GeminiProvider(runtime)
    if runtime.provider_id == 'claude_cli':
        from .claude_provider import ClaudeProvider
        return ClaudeProvider(runtime)
    raise AIFailure('AI_ADAPTER_NOT_READY')
