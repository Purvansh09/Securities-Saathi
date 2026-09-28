"""App configuration, loaded from environment variables / .env.

See CLAUDE.md rule 14: secrets never get committed. This file only reads
them; .env itself is gitignored and .env.example documents what's needed.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    supabase_url: str
    supabase_jwt_secret: str
    database_url: str
    cors_origins: str = "http://localhost:5173"
    internal_sync_secret: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    # lru_cache makes this a singleton: Settings() (and its env parsing) runs once per process.
    return Settings()
