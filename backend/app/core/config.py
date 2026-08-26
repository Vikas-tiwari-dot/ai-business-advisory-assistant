"""
Central application settings.

Everything is read from environment variables (see .env.example at the repo root).
No secret ever has a real default here -- only local-dev-safe fallbacks (SQLite,
AI_PROVIDER=none) so the app runs out of the box with zero external credentials.
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- App ---
    app_name: str = "RazorRecover AI"
    app_version: str = "0.1.0-phase2"
    environment: str = Field(default="local")  # local | staging | production

    # --- Database ---
    # SQLite fallback keeps the whole app runnable with zero setup. Set DATABASE_URL
    # to a postgres:// DSN in production. Same SQLAlchemy models work on both --
    # no Postgres-only column types are used anywhere in app/models.
    database_url: str = Field(default="sqlite:///./razorrecover.db")

    # --- AI provider abstraction ---
    # "none" forces the deterministic fallback_provider, so `docker compose up`
    # or a bare `uvicorn` boot works without any API key.
    ai_provider: str = Field(default="none")  # gemini | openai | none
    gemini_api_key: str | None = Field(default=None)
    openai_api_key: str | None = Field(default=None)
    ai_timeout_seconds: float = Field(default=8.0)

    # --- Razorpay Test Mode ---
    razorpay_key_id: str | None = Field(default=None)
    razorpay_key_secret: str | None = Field(default=None)
    razorpay_webhook_secret: str | None = Field(default=None)
    # When false (default, no keys present) all payment actions route through the
    # local RecoverySimulator instead of the real Razorpay Test Mode API.
    use_razorpay_test_mode: bool = Field(default=False)

    # --- Policy engine bounds (documented here so they're not buried in code) ---
    max_recovery_attempts: int = Field(default=3)
    low_confidence_threshold: float = Field(default=0.55)
    # ₹15,000 -- chosen so the spec §22 reference demo (a ₹12,999 recovery)
    # auto-approves without requiring human sign-off, while still gating
    # genuinely large payments. Tune freely; this is a business parameter,
    # not a fixed constant -- see docs/architecture.md §7 for the config note.
    high_value_escalation_threshold: int = Field(default=1_500_000)  # minor units (₹15,000)

    # --- Evaluation / false-positive cost model (spec §8) ---
    # ₹15 per unnecessarily-attempted recovery action -- a rough estimate of
    # combined payment-processing fee overhead and customer-friction cost for
    # a retry/reminder/alternate-method attempt that turns out not to have
    # been recoverable. Deliberately a named, visible constant rather than a
    # number buried in the evaluation code, per the Phase 1 design note.
    false_positive_unit_cost: int = Field(default=1_500)  # minor units (₹15)

    # --- CORS ---
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
