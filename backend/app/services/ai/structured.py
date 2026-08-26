"""
The one function through which every LLM call in the app must pass to produce
structured data. Enforces:

  1. Raw text -> parsed JSON -> validated against a Pydantic schema.
  2. Exactly one retry on failure (malformed JSON, schema violation, or
     provider error), per spec §14.
  3. Never raises past this boundary -- returns (None, metadata) on final
     failure so callers can fall back deterministically instead of crashing
     the request. The metadata always says why.

This module is intentionally the only place that knows how to strip markdown
fences or call json.loads on model output -- no other module should be doing
its own ad-hoc parsing of LLM text.
"""
import json
import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.services.ai.provider import LLMProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # Drop the opening fence (with optional language tag) and closing fence.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def call_structured(
    provider: LLMProvider,
    prompt: str,
    schema: type[T],
    *,
    max_retries: int = 1,
    timeout: float = 8.0,
) -> tuple[T | None, dict]:
    """
    Returns (parsed_object_or_None, metadata). metadata always includes
    `schema_valid`, `attempts`, and `raw_output` (last attempt's raw text, if
    any was received) so the caller can log/audit exactly what happened even
    on failure.
    """
    last_error: Exception | None = None
    last_raw: str | None = None

    for attempt in range(max_retries + 1):
        try:
            raw = provider.complete(prompt, timeout=timeout)
            last_raw = raw
            cleaned = _strip_markdown_fences(raw)
            data = json.loads(cleaned)
            parsed = schema.model_validate(data)
            return parsed, {
                "schema_valid": True,
                "raw_output": raw,
                "attempts": attempt + 1,
                "provider": provider.name,
            }
        except json.JSONDecodeError as exc:
            last_error = exc
            logger.warning("AI output not valid JSON (attempt %d/%d): %s", attempt + 1, max_retries + 1, exc)
        except ValidationError as exc:
            last_error = exc
            logger.warning("AI output failed schema validation (attempt %d/%d): %s", attempt + 1, max_retries + 1, exc)
        except Exception as exc:  # provider transport/timeout errors, etc.
            last_error = exc
            logger.warning("AI provider call failed (attempt %d/%d): %s", attempt + 1, max_retries + 1, exc)

    return None, {
        "schema_valid": False,
        "raw_output": last_raw,
        "attempts": max_retries + 1,
        "provider": provider.name,
        "error": str(last_error) if last_error else "unknown error",
    }
