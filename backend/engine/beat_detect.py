"""
Audio beat and onset detection using FFmpeg.

Detects:
  - Beat positions (rhythmic hits in music)
  - Audio energy envelope (loudness over time)
  - Silence regions (potential dramatic pauses)
  - BPM estimation

Uses FFmpeg's audio filters:
  - silencedetect: find silent regions
  - astats: per-frame audio statistics
  - ebur128: loudness metering

No external audio libraries required — pure FFmpeg subprocess analysis.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class Beat:
    """A detected beat/onset in the audio."""

    timestamp: float  # seconds
    strength: float  # 0.0-1.0 (how strong the beat is)


@dataclass
class SilenceRegion:
    """A region of silence in the audio."""

    start: float
    end: float
    duration: float


@dataclass
class EnergySegment:
    """Audio energy level for a time segment."""

    start: float
    end: float
    level: float  # 0.0 (silent) to 1.0 (max loudness)


@dataclass
class AudioAnalysis:
    """Complete audio/beat analysis for a single video clip."""

    filename: str
    duration: float
    beats: list[Beat] = field(default_factory=list)
    silences: list[SilenceRegion] = field(default_factory=list)
    energy_segments: list[EnergySegment] = field(default_factory=list)
    estimated_bpm: float = 0.0
    has_music: bool = False
    has_dialogue: bool = False
    avg_loudness: float = 0.0

    def to_dict(self) -> dict:
        """Serialize for JSON/AI consumption."""
        return {
            "filename": self.filename,
            "duration": round(self.duration, 2),
            "estimated_bpm": round(self.estimated_bpm, 1),
            "has_music": self.has_music,
            "has_dialogue": self.has_dialogue,
            "avg_loudness": round(self.avg_loudness, 2),
            "beat_count": len(self.beats),
            "beats": [
                {"timestamp": round(b.timestamp, 3), "strength": round(b.strength, 2)}
                for b in self.beats[:50]  # Limit for AI context
            ],
            "silences": [
                {
                    "start": round(s.start, 2),
                    "end": round(s.end, 2),
                    "duration": round(s.duration, 2),
                }
                for s in self.silences
            ],
            "energy_curve": [
                {
                    "start": round(e.start, 2),
                    "end": round(e.end, 2),
                    "level": round(e.level, 3),
                }
                for e in self.energy_segments
            ],
        }


class BeatDetector:
    """
    Analyzes audio tracks for beats, energy, and silence.

    Strategy for beat detection without external libraries:
      1. Extract audio energy envelope at high resolution (20 samples/sec)
      2. Find onsets by detecting sudden energy increases
      3. Estimate BPM from onset intervals
      4. Detect silence regions for dramatic pauses

    This is a heuristic approach — not as accurate as librosa/madmom,
    but works without any Python audio dependencies.
    """

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        silence_threshold_db: float = -35.0,
        silence_min_duration: float = 0.3,
        energy_sample_rate: float = 20.0,  # samples per second
    ):
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        self.silence_threshold_db = silence_threshold_db
        self.silence_min_duration = silence_min_duration
        self.energy_sample_rate = energy_sample_rate

    def analyze(self, video_path: str) -> AudioAnalysis:
        """
        Run full audio analysis on a video file.

        Returns AudioAnalysis with beats, energy, silences.
        """
        filename = Path(video_path).name
        duration = self._get_duration(video_path)

        logger.info(f"Analyzing audio: {filename} ({duration:.1f}s)")

        # Check if video has audio stream
        if not self._has_audio(video_path):
            logger.info(f"  {filename}: no audio stream detected")
            return AudioAnalysis(filename=filename, duration=duration)

        # 1. Detect silences
        silences = self._detect_silences(video_path, duration)

        # 2. Extract energy envelope
        energy_segments = self._extract_energy(video_path, duration)

        # 3. Detect beats from energy onsets
        beats = self._detect_beats_from_energy(energy_segments)

        # 4. Estimate BPM from beat intervals
        bpm = self._estimate_bpm(beats)

        # 5. Determine content type (music vs dialogue)
        has_music = bpm > 60 and len(beats) > 10
        has_dialogue = len(silences) > 2 and not has_music

        # 6. Average loudness
        avg_loudness = 0.0
        if energy_segments:
            avg_loudness = sum(e.level for e in energy_segments) / len(energy_segments)

        analysis = AudioAnalysis(
            filename=filename,
            duration=duration,
            beats=beats,
            silences=silences,
            energy_segments=energy_segments,
            estimated_bpm=bpm,
            has_music=has_music,
            has_dialogue=has_dialogue,
            avg_loudness=avg_loudness,
        )

        logger.info(
            f"  {filename}: {len(beats)} beats, BPM={bpm:.0f}, "
            f"music={has_music}, dialogue={has_dialogue}, "
            f"{len(silences)} silences"
        )

        return analysis

    def analyze_multiple(self, video_paths: list[str]) -> list[AudioAnalysis]:
        """Analyze audio in multiple videos."""
        results = []
        for path in video_paths:
            if os.path.exists(path):
                try:
                    result = self.analyze(path)
                    results.append(result)
                except Exception as e:
                    logger.warning(f"Audio analysis failed for {path}: {e}")
                    results.append(AudioAnalysis(
                        filename=Path(path).name,
                        duration=self._get_duration(path),
                    ))
        return results

    def _detect_silences(self, video_path: str, duration: float) -> list[SilenceRegion]:
        """Detect silent regions using FFmpeg silencedetect filter."""
        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-af", (
                f"silencedetect=noise={self.silence_threshold_db}dB"
                f":d={self.silence_min_duration}"
            ),
            "-vn",
            "-f", "null",
            "-",
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=min(duration + 30, 90),
            )
            stderr = result.stderr
        except subprocess.TimeoutExpired:
            return []

        # Parse silence_start and silence_end
        silences = []
        starts = re.finditer(r"silence_start:\s*([\d.]+)", stderr)
        ends = re.finditer(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)", stderr)

        start_times = [float(m.group(1)) for m in starts]
        end_data = [(float(m.group(1)), float(m.group(2))) for m in ends]

        for i, (end_time, dur) in enumerate(end_data):
            start_time = start_times[i] if i < len(start_times) else end_time - dur
            silences.append(SilenceRegion(
                start=start_time,
                end=end_time,
                duration=dur,
            ))

        return silences

    def _extract_energy(self, video_path: str, duration: float) -> list[EnergySegment]:
        """
        Extract audio energy envelope using volumedetect on small segments.

        Strategy: Use astats filter to get RMS per frame, then aggregate
        into segments at the configured sample rate.
        """
        # Extract raw audio volume data using the showvolume-like approach
        # We use volumedetect on the whole file and astats for per-frame data
        interval = 1.0 / self.energy_sample_rate

        # Use ebur128 for integrated loudness at intervals
        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-af", f"asetnsamples=n={int(48000 * interval)},astats=metadata=1:reset=1",
            "-vn",
            "-f", "null",
            "-",
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=min(duration * 2 + 30, 120),
            )
            stderr = result.stderr
        except subprocess.TimeoutExpired:
            return self._fallback_energy(duration)

        # Parse RMS level from astats output
        # Format: [Parsed_astats...] lavfi.astats.Overall.RMS_level=-23.456
        rms_values = []
        pattern = r"lavfi\.astats\.Overall\.RMS_level=([-\d.]+)"
        for match in re.finditer(pattern, stderr):
            rms_db = float(match.group(1))
            rms_values.append(rms_db)

        if not rms_values:
            return self._fallback_energy(duration)

        # Convert dB to linear 0-1 scale
        # -60dB = silence, 0dB = max
        segments = []
        for i, rms_db in enumerate(rms_values):
            start = i * interval
            end = min((i + 1) * interval, duration)

            # Clamp and normalize: -60dB→0.0, 0dB→1.0
            normalized = max(0.0, min(1.0, (rms_db + 60.0) / 60.0))
            segments.append(EnergySegment(start=start, end=end, level=normalized))

        # Downsample to reasonable resolution (max 200 segments for AI context)
        if len(segments) > 200:
            segments = self._downsample_energy(segments, 200)

        return segments

    def _detect_beats_from_energy(self, energy: list[EnergySegment]) -> list[Beat]:
        """
        Detect beats by finding sudden energy increases (onsets).

        An onset is where energy[i] - energy[i-1] exceeds a threshold,
        indicating a percussive hit or strong transient.
        """
        if len(energy) < 3:
            return []

        beats = []
        onset_threshold = 0.15  # minimum energy jump to count as beat

        # Compute energy differences (first derivative)
        for i in range(1, len(energy)):
            diff = energy[i].level - energy[i - 1].level

            # Positive onset: energy jumped up
            if diff > onset_threshold and energy[i].level > 0.3:
                timestamp = energy[i].start
                # Strength based on the jump magnitude
                strength = min(1.0, diff / 0.5)
                beats.append(Beat(timestamp=timestamp, strength=strength))

        # Remove beats too close together (< 0.1s apart = likely noise)
        filtered = []
        for beat in beats:
            if not filtered or (beat.timestamp - filtered[-1].timestamp) > 0.1:
                filtered.append(beat)

        return filtered

    def _estimate_bpm(self, beats: list[Beat]) -> float:
        """
        Estimate BPM from beat intervals.

        Takes the median interval between consecutive beats and converts to BPM.
        """
        if len(beats) < 4:
            return 0.0

        intervals = []
        for i in range(1, len(beats)):
            interval = beats[i].timestamp - beats[i - 1].timestamp
            if 0.2 < interval < 2.0:  # Reasonable beat interval range (30-300 BPM)
                intervals.append(interval)

        if not intervals:
            return 0.0

        # Use median for robustness
        intervals.sort()
        median_interval = intervals[len(intervals) // 2]

        bpm = 60.0 / median_interval

        # Sanity check: most music is 60-180 BPM
        if bpm < 40 or bpm > 220:
            # Try halving or doubling
            if bpm > 220:
                bpm /= 2
            elif bpm < 40:
                bpm *= 2

        return bpm

    def _fallback_energy(self, duration: float) -> list[EnergySegment]:
        """Generate uniform energy when analysis fails."""
        interval = 0.5
        segments = []
        t = 0.0
        while t < duration:
            end = min(t + interval, duration)
            segments.append(EnergySegment(start=t, end=end, level=0.5))
            t = end
        return segments

    def _downsample_energy(
        self, segments: list[EnergySegment], target_count: int
    ) -> list[EnergySegment]:
        """Reduce number of energy segments by averaging groups."""
        if len(segments) <= target_count:
            return segments

        group_size = len(segments) / target_count
        result = []

        for i in range(target_count):
            start_idx = int(i * group_size)
            end_idx = int((i + 1) * group_size)
            group = segments[start_idx:end_idx]

            if group:
                avg_level = sum(s.level for s in group) / len(group)
                result.append(EnergySegment(
                    start=group[0].start,
                    end=group[-1].end,
                    level=avg_level,
                ))

        return result

    def _has_audio(self, video_path: str) -> bool:
        """Check if video file contains an audio stream."""
        cmd = [
            self.ffprobe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "a",
            video_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=10
            )
            data = json.loads(result.stdout)
            return len(data.get("streams", [])) > 0
        except Exception:
            return False

    def _get_duration(self, video_path: str) -> float:
        """Get video duration using ffprobe."""
        cmd = [
            self.ffprobe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            video_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=15
            )
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except Exception:
            return 10.0
