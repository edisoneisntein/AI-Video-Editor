"""
Base interface for AI video analysis providers.

All providers must implement the AIProvider protocol to be interchangeable.
This enables multi-provider support (Gemini, NVIDIA NIM, OpenAI, etc.)
without changing calling code.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AnalysisRequest:
    """Request parameters for video analysis — provider-agnostic."""

    video_paths: list[str]
    genre: str = "drama"
    rhythm: str = "medio"
    reference: str = ""
    tone: str = ""
    duration_target: str = ""
    additional_instructions: str = ""
    language: str = "es"

    # Scene and audio metadata (populated by pre-analysis)
    # List of dicts from VideoAnalysis.to_dict() — one per clip
    scene_data: list[dict] | None = None
    # List of dicts from AudioAnalysis.to_dict() — one per clip
    audio_data: list[dict] | None = None
    # List of dicts from SpeechAnalysis.to_dict() — one per clip
    speech_data: list[dict] | None = None
    # List of dicts from FragmentedClip.to_dict() — one per clip
    fragment_data: list[dict] | None = None


@dataclass
class AnalysisResult:
    """Result from video analysis — provider-agnostic."""

    edit_plan: dict
    raw_response: str
    model_used: str = ""
    provider: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    processing_time: float = 0.0


class AIProvider(ABC):
    """
    Abstract base class for AI video analysis providers.

    Implementations:
      - GeminiProvider (Google Gemini 2.5 Flash/Pro)
      - NvidiaProvider (NVIDIA NIM — Nemotron, Qwen, MiniMax, etc.)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g., 'gemini', 'nvidia')."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Currently configured model name."""
        ...

    @abstractmethod
    def analyze_videos(self, request: AnalysisRequest) -> AnalysisResult:
        """
        Analyze video clips and return a structured edit plan.

        Args:
            request: Analysis parameters and video file paths

        Returns:
            AnalysisResult with edit_plan dict and metadata
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is properly configured and reachable."""
        ...
