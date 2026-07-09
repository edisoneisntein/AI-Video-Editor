"""
Provider factory — selects and instantiates the appropriate AI provider
based on configuration and availability.

Supports:
  - "gemini"  → Google Gemini API (default)
  - "nvidia"  → NVIDIA NIM API
  - "auto"    → Use first available (gemini first, nvidia fallback)

Configuration via .env:
  AI_PROVIDER=gemini|nvidia|auto
  GEMINI_API_KEY=...
  NVIDIA_API_KEY=...
  NVIDIA_MODEL=nvidia/nemotron-3-nano-omni-30b-a3b-reasoning
"""

import os

from loguru import logger

from backend.ai.base import AIProvider
from backend.config import get_settings


def get_provider(provider_name: str | None = None) -> AIProvider:
    """
    Get an AI provider instance by name.

    Args:
        provider_name: "gemini", "nvidia", or "auto" (default from config)

    Returns:
        Configured AIProvider instance

    Raises:
        ValueError: If requested provider is not configured/available
    """
    settings = get_settings()
    name = (provider_name or os.getenv("AI_PROVIDER", "auto")).lower().strip()

    if name == "gemini":
        return _get_gemini_provider(settings)
    elif name == "nvidia":
        return _get_nvidia_provider(settings)
    elif name == "auto":
        return _get_auto_provider(settings)
    else:
        raise ValueError(
            f"Unknown AI provider: '{name}'. "
            f"Supported: gemini, nvidia, auto"
        )


def list_providers() -> list[dict]:
    """
    List all configured providers and their availability status.

    Returns:
        List of {name, model, available, reason}
    """
    settings = get_settings()
    providers = []

    # Gemini
    gemini_key = settings.gemini_api_key
    gemini_ok = bool(gemini_key and gemini_key != "your_gemini_api_key_here")
    providers.append({
        "name": "gemini",
        "model": settings.gemini_model,
        "available": gemini_ok,
        "reason": "Configured" if gemini_ok else "GEMINI_API_KEY not set",
    })

    # NVIDIA
    nvidia_key = settings.nvidia_api_key
    nvidia_ok = bool(nvidia_key and nvidia_key != "your_nvidia_api_key_here")
    nvidia_model = settings.nvidia_model
    providers.append({
        "name": "nvidia",
        "model": nvidia_model,
        "available": nvidia_ok,
        "reason": "Configured" if nvidia_ok else "NVIDIA_API_KEY not set",
    })

    return providers


def _get_gemini_provider(settings) -> AIProvider:
    """Instantiate Gemini provider."""
    from backend.ai.gemini_client import GeminiProvider

    if not settings.gemini_api_key or settings.gemini_api_key == "your_gemini_api_key_here":
        raise ValueError("Gemini provider requested but GEMINI_API_KEY not configured")

    return GeminiProvider(
        api_key=settings.gemini_api_key,
        model_name=settings.gemini_model,
    )


def _get_nvidia_provider(settings) -> AIProvider:
    """Instantiate NVIDIA NIM provider."""
    from backend.ai.nvidia_client import NvidiaProvider

    nvidia_key = settings.nvidia_api_key
    if not nvidia_key or nvidia_key == "your_nvidia_api_key_here":
        raise ValueError("NVIDIA provider requested but NVIDIA_API_KEY not configured")

    return NvidiaProvider(
        api_key=nvidia_key,
        model_name=settings.nvidia_model,
        ffmpeg_path=settings.ffmpeg_path,
        ffprobe_path=settings.ffprobe_path,
    )


def _get_auto_provider(settings) -> AIProvider:
    """
    Auto-select: try Gemini first, fallback to NVIDIA.
    Returns the first available provider.
    """
    # Try Gemini first (native video upload, no FFmpeg needed for analysis)
    try:
        provider = _get_gemini_provider(settings)
        if provider.is_available():
            logger.info(f"Auto-selected provider: gemini ({provider.model_name})")
            return provider
    except ValueError:
        pass

    # Fallback to NVIDIA
    try:
        provider = _get_nvidia_provider(settings)
        if provider.is_available():
            logger.info(f"Auto-selected provider: nvidia ({provider.model_name})")
            return provider
    except ValueError:
        pass

    raise ValueError(
        "No AI provider available. Configure at least one:\n"
        "  - GEMINI_API_KEY for Google Gemini\n"
        "  - NVIDIA_API_KEY for NVIDIA NIM\n"
        "Set AI_PROVIDER=gemini|nvidia|auto in .env"
    )
