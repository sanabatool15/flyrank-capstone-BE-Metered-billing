"""Application settings, read from environment variables (.env in dev).

Never hardcode secrets here — see CLAUDE.md.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase Postgres connection string (wire-compatible Postgres — see
    # Settings -> Database -> Connection string in the Supabase dashboard).
    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/billing"

    # Supabase project settings (Settings -> API in the Supabase dashboard).
    # SUPABASE_URL / SUPABASE_ANON_KEY are used by any future supabase-py client
    # code (e.g. storage, realtime); SUPABASE_SERVICE_ROLE_KEY is for trusted
    # server-side calls only — never exposed to clients.
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    # Settings -> API -> JWT Settings -> JWT Secret. Used to verify the
    # signature of Supabase Auth access tokens (HS256) sent as the bearer
    # token on every request. Tests override this with a known test secret.
    SUPABASE_JWT_SECRET: str = ""

    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_ID_PRO: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
