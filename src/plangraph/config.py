"""All configuration in one place. Values come from environment variables or a .env file."""

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_url: str = "postgresql+psycopg://plangraph:plangraph@localhost:5432/plangraph"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "plangraph-dev"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"

    # "Today" for the simulated company. None means the real today.
    as_of: date | None = None

    raw_dir: Path = Path("data/raw")
    docs_dir: Path = Path("data/docs")
    artifacts_dir: Path = Path("artifacts")

    api_url: str = "http://localhost:8000"

    @field_validator("as_of", mode="before")
    @classmethod
    def _empty_as_none(cls, value):
        return value or None

    @property
    def llm_enabled(self) -> bool:
        return bool(self.gemini_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
