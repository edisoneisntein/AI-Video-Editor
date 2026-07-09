"""
Application configuration using pydantic-settings.
Loads from .env file and environment variables.
"""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Keys
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # NVIDIA
    nvidia_api_key: str = ""
    nvidia_model: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"

    # AI Provider Selection
    ai_provider: str = "auto"

    # API Authentication
    api_secret_key: str = "change_this_to_a_strong_random_secret"

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    debug: bool = False

    # CORS
    cors_origins: str = "http://localhost:8501,http://127.0.0.1:8501"

    # Storage
    upload_dir: str = "./storage/uploads"
    output_dir: str = "./storage/outputs"
    temp_dir: str = "./storage/temp"
    max_upload_size_mb: int = 500

    # Database
    database_url: str = "sqlite:///./storage/projects.db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # FFmpeg
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    # Video Output
    output_resolution: str = "1920x1080"
    output_fps: int = 24
    output_codec: str = "libx264"
    output_preset: str = "slow"
    output_crf: int = 18

    # Rate Limiting
    rate_limit_per_minute: int = 30

    # Concurrency
    max_concurrent_renders: int = 2

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    def ensure_directories(self):
        """Create storage directories if they don't exist."""
        for dir_path in [self.upload_dir, self.output_dir, self.temp_dir]:
            os.makedirs(dir_path, exist_ok=True)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()
    settings.ensure_directories()
    return settings
