"""
Single choke point for "which LLM (if any) is active." Returns None whenever
there's no usable provider -- missing key, unset AI_PROVIDER, or an explicit
AI_PROVIDER=none -- so callers have one clean way to detect "run the
deterministic fallback path" rather than checking config in three places.
"""
from app.core.config import Settings
from app.services.ai.gemini_provider import GeminiProvider
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.provider import LLMProvider


def get_provider(settings: Settings) -> LLMProvider | None:
    if settings.ai_provider == "gemini" and settings.gemini_api_key:
        return GeminiProvider(settings.gemini_api_key)
    if settings.ai_provider == "openai" and settings.openai_api_key:
        return OpenAIProvider(settings.openai_api_key)
    return None
