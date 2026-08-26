"""
Gemini provider. Not exercised by the automated test suite in this sandbox --
outbound network access here is locked to package registries only, so this
implementation is written to the documented Gemini REST API but has not been
run against a live endpoint. Exercise it manually with a real GEMINI_API_KEY
before relying on it for a demo.
"""
import httpx

from app.services.ai.provider import LLMProvider

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model = model

    def complete(self, prompt: str, timeout: float) -> str:
        url = GEMINI_ENDPOINT.format(model=self.model)
        response = httpx.post(
            url,
            params={"key": self.api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected Gemini response shape: {data}") from exc
