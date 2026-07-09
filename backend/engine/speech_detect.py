"""
Speech Boundary Detector.

Finds phrase endings in dialogue — the 200-400ms pauses between sentences
where a cut feels natural and invisible.

Unlike the beat detector (which finds musical hits), this module specifically
targets SPEECH PATTERNS:
  - End of sentences (falling intonation + pause)
  - Natural breath pauses between phrases
  - Speaker transitions

Uses FFmpeg silencedetect with fine-tuned thresholds for speech (not music).
"""

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class SpeechPause:
    """A pause between phrases — optimal cut point for dialogue."""

    timestamp: float  # center of the pause (best cut point)
    start: float  # where silence begins
    end: float  # where next phrase starts
    duration: float  # length of the pause
    confidence: float  # 0.0-1.0 how likely this is a phrase boundary

    def to_dict(self) -> dict:
        return {
            "timestamp": round(self.timestamp, 3),
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "confidence": round(self.confidence, 2),
        }


@dataclass
class SpeechAnalysis:
    """Speech boundary analysis for a video clip."""

    filename: str
    duration: float
    has_speech: bool
    phrase_pauses: list[SpeechPause] = field(default_factory=list)
    avg_phrase_length: float = 0.0  # average time between pauses

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "duration": round(self.duration, 2),
            "has_speech": self.has_speech,
            "phrase_pause_count": len(self.phrase_pauses),
            "avg_phrase_length": round(self.avg_phrase_length, 2),
            "phrase_pauses": [p.to_dict() for p in self.phrase_pauses],
        }


class SpeechBoundaryDetector:
    """
    Detects phrase boundaries in speech using FFmpeg silence analysis.

    Strategy:
      1. Run silencedetect with speech-appropriate thresholds:
         - Noise threshold: -25dB (catches breath pauses, not just dead silence)
         - Min duration: 0.15s (speech pauses are 150-600ms typically)
      2. Filter results to find PHRASE boundaries (not just any silence):
         - Duration 0.15s-1.5s = likely phrase pause (confidence high)
         - Duration 0.05s-0.15s = likely breath (confidence low)
         - Duration > 1.5s = likely scene pause or speaker change (confidence medium)
      3. Calculate optimal cut point (center of pause, not start or end)
    """

    # Thresholds tuned for speech detection
    NOISE_THRESHOLD_DB = -25.0  # Softer than music detection (-35dB)
    MIN_SILENCE_DURATION = 0.12  # 120ms catches short breath pauses
    MAX_PHRASE_PAUSE = 2.0  # Pauses longer than this aren't phrase boundaries

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
    ):
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path

    def analyze(self, video_path: str) -> SpeechAnalysis:
        """
        Analyze a video for speech phrase boundaries.

        Returns SpeechAnalysis with optimal cut points between phrases.
        """
        filename = Path(video_path).name
        duration = self._get_duration(video_path)

        if not self._has_audio(video_path):
            return SpeechAnalysis(
                filename=filename, duration=duration, has_speech=False
            )

        # Run silence detection with speech thresholds
        raw_silences = self._detect_speech_silences(video_path, duration)

        if not raw_silences:
            return SpeechAnalysis(
                filename=filename, duration=duration, has_speech=False
            )

        # Classify silences into phrase pauses
        phrase_pauses = self._classify_pauses(raw_silences, duration)

        # Determine if this is actually speech (vs music or ambient)
        has_speech = self._is_likely_speech(phrase_pauses, duration)

        # Calculate average phrase length
        avg_phrase_length = 0.0
        if len(phrase_pauses) >= 2:
            intervals = [
                phrase_pauses[i + 1].timestamp - phrase_pauses[i].timestamp
                for i in range(len(phrase_pauses) - 1)
            ]
            avg_phrase_length = sum(intervals) / len(intervals)

        analysis = SpeechAnalysis(
            filename=filename,
            duration=duration,
            has_speech=has_speech,
            phrase_pauses=phrase_pauses,
            avg_phrase_length=avg_phrase_length,
        )

        logger.info(
            f"  {filename}: speech={'YES' if has_speech else 'NO'}, "
            f"{len(phrase_pauses)} phrase pauses, "
            f"avg phrase={avg_phrase_length:.1f}s"
        )

        return analysis

    def analyze_multiple(self, video_paths: list[str]) -> list[SpeechAnalysis]:
        """Analyze multiple videos for speech boundaries."""
        results = []
        for path in video_paths:
            if os.path.exists(path):
                try:
                    results.append(self.analyze(path))
                except Exception as e:
                    logger.warning(f"Speech detection failed for {path}: {e}")
                    results.append(SpeechAnalysis(
                        filename=Path(path).name,
                        duration=self._get_duration(path),
                        has_speech=False,
                    ))
            else:
                results.append(SpeechAnalysis(
                    filename=Path(path).name, duration=0, has_speech=False
                ))
        return results

    def _detect_speech_silences(
        self, video_path: str, duration: float
    ) -> list[tuple[float, float]]:
        """
        Run FFmpeg silencedetect with speech-tuned parameters.
        Returns list of (start, end) tuples for each silence.
        """
        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-af", (
                f"silencedetect=noise={self.NOISE_THRESHOLD_DB}dB"
                f":d={self.MIN_SILENCE_DURATION}"
            ),
            "-vn",
            "-f", "null",
            "-",
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=min(duration * 2 + 20, 90),
            )
            stderr = result.stderr
        except subprocess.TimeoutExpired:
            return []

        # Parse silence regions
        silences = []
        starts = [float(m.group(1)) for m in re.finditer(r"silence_start:\s*([\d.]+)", stderr)]
        ends = re.finditer(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)", stderr)

        end_data = [(float(m.group(1)), float(m.group(2))) for m in ends]

        for i, (end_time, dur) in enumerate(end_data):
            start_time = starts[i] if i < len(starts) else end_time - dur
            silences.append((start_time, end_time))

        return silences

    def _classify_pauses(
        self, silences: list[tuple[float, float]], duration: float
    ) -> list[SpeechPause]:
        """
        Classify detected silences into speech phrase boundaries.

        Confidence based on duration:
          - 0.2-0.6s: HIGH confidence phrase boundary (natural speech pause)
          - 0.15-0.2s: MEDIUM confidence (might be breath)
          - 0.6-1.5s: MEDIUM confidence (longer pause, maybe emphasis)
          - >1.5s: LOW confidence (scene break, not phrase)
        """
        pauses = []

        for start, end in silences:
            pause_duration = end - start

            # Skip too-short or too-long
            if pause_duration < 0.1 or pause_duration > self.MAX_PHRASE_PAUSE:
                continue

            # Skip silences at very start/end of clip
            if start < 0.3 or end > duration - 0.3:
                continue

            # Calculate confidence
            if 0.2 <= pause_duration <= 0.6:
                confidence = 0.9  # Sweet spot for phrase boundaries
            elif 0.15 <= pause_duration < 0.2:
                confidence = 0.6  # Might be a breath
            elif 0.6 < pause_duration <= 1.0:
                confidence = 0.75  # Deliberate pause
            elif 1.0 < pause_duration <= self.MAX_PHRASE_PAUSE:
                confidence = 0.5  # Long pause — paragraph break or speaker change
            else:
                confidence = 0.3

            # Optimal cut point: slightly after silence starts
            # (cut AFTER the last word ends, not in the middle of silence)
            cut_point = start + (pause_duration * 0.3)

            pauses.append(SpeechPause(
                timestamp=cut_point,
                start=start,
                end=end,
                duration=pause_duration,
                confidence=confidence,
            ))

        return pauses

    def _is_likely_speech(
        self, pauses: list[SpeechPause], duration: float
    ) -> bool:
        """
        Determine if the audio contains speech based on pause patterns.

        Speech typically has:
          - Regular pauses every 2-8 seconds
          - Pause durations mostly 0.2-0.8s
          - At least 3 pauses per 30 seconds of audio
        """
        if not pauses:
            return False

        # Need minimum density of pauses
        pause_density = len(pauses) / max(duration, 1.0) * 30.0  # per 30s
        if pause_density < 2:
            return False

        # Most pauses should be in speech range (0.15-1.0s)
        speech_range_count = sum(
            1 for p in pauses if 0.15 <= p.duration <= 1.0
        )
        speech_ratio = speech_range_count / len(pauses)

        return speech_ratio > 0.5

    def _has_audio(self, video_path: str) -> bool:
        """Check if video has audio stream."""
        cmd = [
            self.ffprobe, "-v", "quiet",
            "-print_format", "json",
            "-show_streams", "-select_streams", "a",
            video_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=10
            )
            import json
            data = json.loads(result.stdout)
            return len(data.get("streams", [])) > 0
        except Exception:
            return False

    def _get_duration(self, video_path: str) -> float:
        """Get video duration."""
        cmd = [
            self.ffprobe, "-v", "quiet",
            "-print_format", "json", "-show_format", video_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=15
            )
            import json
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except Exception:
            return 10.0
