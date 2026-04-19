"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR: Path = Path(__file__).resolve().parent
STORAGE_DIR: Path = BASE_DIR / "storage"
UPLOADS_DIR: Path = STORAGE_DIR / "uploads"
CLIPS_DIR: Path = STORAGE_DIR / "clips"
TEMP_DIR: Path = STORAGE_DIR / "temp"
DB_PATH: Path = STORAGE_DIR / "clipkar.db"


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    MAX_UPLOAD_MB: int = 500

    ALLOWED_EXTENSIONS: tuple[str, ...] = (".mp4", ".mov", ".webm", ".mkv")

    WHISPER_MODEL: str = "whisper-1"
    CLAUDE_MODEL: str = "claude-haiku-4-5-20251001"

    FRONTEND_ORIGIN: str = "http://localhost:3000"

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024


settings = Settings()


def ensure_directories() -> None:
    """Create storage directories if they do not exist."""
    for directory in (STORAGE_DIR, UPLOADS_DIR, CLIPS_DIR, TEMP_DIR):
        directory.mkdir(parents=True, exist_ok=True)
