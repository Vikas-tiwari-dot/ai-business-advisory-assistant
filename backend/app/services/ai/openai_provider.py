"""
OpenAI provider. Same caveat as gemini_provider.py: written to the documented
Chat Completions API but not exercised live in this sandbox (network egress
here is restricted to package registries). Test manually with a real
OPENAI_API_KEY before depending on it for a demo.
"""
import httpx

from app.services.ai.provider import LLMProvider

OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.model = model

    def complete(self, prompt: str, timeout: float) -> str:
        response = httpx.post(
            OPENAI_ENDPOINT,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected OpenAI response shape: {data}") from exc
