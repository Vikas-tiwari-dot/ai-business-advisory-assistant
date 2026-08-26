from pydantic import BaseModel

from app.services.ai.provider import LLMProvider
from app.services.ai.structured import call_structured


class DummySchema(BaseModel):
    foo: str
    bar: int


class ScriptedProvider(LLMProvider):
    """Returns a scripted sequence of responses, one per call, for testing retries."""

    name = "scripted"

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, prompt: str, timeout: float) -> str:
        self.calls += 1
        response = self._responses.pop(0)
        if response == "__RAISE__":
            raise RuntimeError("simulated provider timeout")
        return response


VALID_JSON = '{"foo": "hello", "bar": 42}'
MALFORMED_JSON = "this is not json at all {{{"
WRONG_SCHEMA_JSON = '{"foo": "hello"}'  # missing required "bar"
FENCED_JSON = f"```json\n{VALID_JSON}\n```"


def test_valid_json_parses_on_first_attempt():
    provider = ScriptedProvider([VALID_JSON])
    parsed, meta = call_structured(provider, "prompt", DummySchema)
    assert parsed is not None
    assert parsed.foo == "hello"
    assert parsed.bar == 42
    assert meta["schema_valid"] is True
    assert meta["attempts"] == 1
    assert provider.calls == 1


def test_markdown_fences_are_stripped():
    provider = ScriptedProvider([FENCED_JSON])
    parsed, meta = call_structured(provider, "prompt", DummySchema)
    assert parsed is not None
    assert parsed.foo == "hello"
    assert meta["schema_valid"] is True


def test_malformed_json_retries_once_then_recovers_if_second_attempt_valid():
    provider = ScriptedProvider([MALFORMED_JSON, VALID_JSON])
    parsed, meta = call_structured(provider, "prompt", DummySchema, max_retries=1)
    assert parsed is not None
    assert meta["attempts"] == 2
    assert provider.calls == 2


def test_malformed_json_on_both_attempts_returns_none_with_metadata():
    """This is the exit-criterion test: intentionally malformed LLM output, twice."""
    provider = ScriptedProvider([MALFORMED_JSON, MALFORMED_JSON])
    parsed, meta = call_structured(provider, "prompt", DummySchema, max_retries=1)
    assert parsed is None
    assert meta["schema_valid"] is False
    assert meta["attempts"] == 2
    assert "error" in meta
    assert provider.calls == 2  # exactly one retry, not unbounded


def test_schema_violation_treated_same_as_malformed_json():
    provider = ScriptedProvider([WRONG_SCHEMA_JSON, WRONG_SCHEMA_JSON])
    parsed, meta = call_structured(provider, "prompt", DummySchema, max_retries=1)
    assert parsed is None
    assert meta["schema_valid"] is False


def test_provider_exception_is_caught_and_retried():
    provider = ScriptedProvider(["__RAISE__", VALID_JSON])
    parsed, meta = call_structured(provider, "prompt", DummySchema, max_retries=1)
    assert parsed is not None
    assert meta["attempts"] == 2


def test_zero_retries_means_single_attempt_only():
    provider = ScriptedProvider([MALFORMED_JSON])
    parsed, meta = call_structured(provider, "prompt", DummySchema, max_retries=0)
    assert parsed is None
    assert meta["attempts"] == 1
    assert provider.calls == 1


def test_never_raises_on_repeated_failure():
    # The whole point of this function is that callers never need a try/except.
    provider = ScriptedProvider(["__RAISE__", "__RAISE__"])
    parsed, meta = call_structured(provider, "prompt", DummySchema, max_retries=1)
    assert parsed is None
    assert meta["schema_valid"] is False
