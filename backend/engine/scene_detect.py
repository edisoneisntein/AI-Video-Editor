"""
Scene detection and motion analysis using FFmpeg.

Detects:
  - Shot boundaries (scene changes) via scdet filter
  - Motion intensity per segment via mestimate filter
  - Key moments (highest visual energy)

All analysis is done via FFmpeg subprocess — no heavy Python libraries needed.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class SceneChange:
    """A detected scene/shot boundary in the video."""

    timestamp: float  # seconds
    score: float  # confidence 0.0-1.0 (higher = more distinct change)


@dataclass
class MotionSegment:
    """Motion intensity for a time segment."""

    start: float  # seconds
    end: float  # seconds
    intensity: float  # 0.0 (static) to 1.0 (extreme motion)


@dataclass
class KeyMoment:
    """A moment of high visual energy (potential cut point)."""

    timestamp: float
    type: str  # "scene_change", "high_motion", "motion_peak", "stillness"
    score: float  # relevance 0.0-1.0


@dataclass
class VideoAnalysis:
    """Complete scene/motion analysis for a single video clip."""

    filename: str
    duration: float
    scene_changes: list[SceneChange] = field(default_factory=list)
    motion_segments: list[MotionSegment] = field(default_factory=list)
    key_moments: list[KeyMoment] = field(default_factory=list)
    avg_motion: float = 0.0
    scene_count: int = 0

    def to_dict(self) -> dict:
        """Serialize for JSON/AI consumption."""
        return {
            "filename": self.filename,
            "duration": round(self.duration, 2),
            "scene_count": self.scene_count,
            "avg_motion_intensity": round(self.avg_motion, 3),
            "scene_changes": [
                {"timestamp": round(sc.timestamp, 2), "score": round(sc.score, 3)}
                for sc in self.scene_changes
            ],
            "motion_segments": [
                {
                    "start": round(ms.start, 2),
                    "end": round(ms.end, 2),
                    "intensity": round(ms.intensity, 3),
                }
                for ms in self.motion_segments
            ],
            "key_moments": [
                {
                    "timestamp": round(km.timestamp, 2),
                    "type": km.type,
                    "score": round(km.score, 3),
                }
                for km in self.key_moments
            ],
        }


class SceneDetector:
    """
    Analyzes video files for scene changes and motion intensity.

    Uses FFmpeg filters:
      - scdet: scene change detection (compares frame similarity)
      - mestimate: motion estimation (optical flow approximation)

    All processing is subprocess-based with timeouts.
    """

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        scene_threshold: float = 0.3,
        motion_sample_interval: float = 0.5,
    ):
        """
        Args:
            ffmpeg_path: Path to ffmpeg binary
            ffprobe_path: Path to ffprobe binary
            scene_threshold: Score above which a frame is a scene change (0.0-1.0)
            motion_sample_interval: Seconds between motion samples
        """
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        self.scene_threshold = scene_threshold
        self.motion_sample_interval = motion_sample_interval

    def analyze(self, video_path: str) -> VideoAnalysis:
        """
        Run full analysis on a video file.

        Returns VideoAnalysis with scene changes, motion data, and key moments.
        """
        filename = Path(video_path).name
        duration = self._get_duration(video_path)

        logger.info(f"Analyzing scenes: {filename} ({duration:.1f}s)")

        # Detect scene changes
        scene_changes = self._detect_scenes(video_path, duration)

        # Analyze motion intensity
        motion_segments = self._analyze_motion(video_path, duration)

        # Compute average motion
        avg_motion = 0.0
        if motion_segments:
            avg_motion = sum(ms.intensity for ms in motion_segments) / len(motion_segments)

        # Identify key moments from combined data
        key_moments = self._identify_key_moments(
            scene_changes, motion_segments, duration
        )

        analysis = VideoAnalysis(
            filename=filename,
            duration=duration,
            scene_changes=scene_changes,
            motion_segments=motion_segments,
            key_moments=key_moments,
            avg_motion=avg_motion,
            scene_count=len(scene_changes),
        )

        logger.info(
            f"  {filename}: {len(scene_changes)} scenes, "
            f"avg motion={avg_motion:.3f}, {len(key_moments)} key moments"
        )

        return analysis

    def analyze_multiple(self, video_paths: list[str]) -> list[VideoAnalysis]:
        """Analyze multiple videos and return results for each."""
        results = []
        for path in video_paths:
            if os.path.exists(path):
                try:
                    result = self.analyze(path)
                    results.append(result)
                except Exception as e:
                    logger.warning(f"Scene detection failed for {path}: {e}")
                    # Return minimal analysis on failure
                    results.append(VideoAnalysis(
                        filename=Path(path).name,
                        duration=self._get_duration(path),
                    ))
        return results

    def _detect_scenes(self, video_path: str, duration: float) -> list[SceneChange]:
        """
        Detect scene/shot boundaries using FFmpeg scdet filter.

        The scdet filter outputs metadata when it detects a scene change,
        with a score indicating confidence.
        """
        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-vf", f"scdet=threshold={self.scene_threshold}:sc_pass=1",
            "-an",  # no audio
            "-f", "null",
            "-",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=min(duration * 2 + 30, 120),
            )
            stderr = result.stderr
        except subprocess.TimeoutExpired:
            logger.warning(f"Scene detection timed out for {video_path}")
            return []

        # Parse scdet output from stderr
        # Format: [scdet @ 0x...] lavfi.scd.time: 5.005000 lavfi.scd.score: 0.854321
        scenes = []
        pattern = r"lavfi\.scd\.time:\s*([\d.]+).*?lavfi\.scd\.score:\s*([\d.]+)"

        for match in re.finditer(pattern, stderr):
            timestamp = float(match.group(1))
            score = float(match.group(2))

            # Normalize score to 0-1 range (scdet outputs 0-100 sometimes)
            if score > 1.0:
                score = score / 100.0

            scenes.append(SceneChange(timestamp=timestamp, score=min(score, 1.0)))

        return scenes

    def _analyze_motion(self, video_path: str, duration: float) -> list[MotionSegment]:
        """
        Estimate motion intensity using frame difference analysis.

        Strategy: extract frames at regular intervals, compute difference
        between consecutive frames using FFmpeg's blend filter.
        Returns motion intensity per segment.
        """
        # Use signalstats filter to get temporal information (YDIF = frame difference)
        interval = self.motion_sample_interval
        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-vf", f"fps=1/{interval},signalstats=stat=tout+vrep+brng",
            "-an",
            "-f", "null",
            "-",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=min(duration * 2 + 30, 120),
            )
            stderr = result.stderr
        except subprocess.TimeoutExpired:
            logger.warning(f"Motion analysis timed out for {video_path}")
            return self._fallback_motion(duration)

        # Parse signalstats YDIF (temporal difference) values
        # Format: [Parsed_signalstats...] YDIF: 12.345
        ydif_values = []
        pattern = r"YDIF:\s*([\d.]+)"
        for match in re.finditer(pattern, stderr):
            ydif_values.append(float(match.group(1)))

        if not ydif_values:
            # Fallback: try simpler approach with frame count
            return self._fallback_motion(duration)

        # Normalize YDIF values to 0-1 range
        max_ydif = max(ydif_values) if ydif_values else 1.0
        if max_ydif == 0:
            max_ydif = 1.0

        segments = []
        for i, ydif in enumerate(ydif_values):
            start = i * interval
            end = min((i + 1) * interval, duration)
            intensity = min(ydif / max_ydif, 1.0)
            segments.append(MotionSegment(start=start, end=end, intensity=intensity))

        return segments

    def _fallback_motion(self, duration: float) -> list[MotionSegment]:
        """Generate uniform motion segments when analysis fails."""
        interval = self.motion_sample_interval
        segments = []
        t = 0.0
        while t < duration:
            end = min(t + interval, duration)
            segments.append(MotionSegment(start=t, end=end, intensity=0.5))
            t = end
        return segments

    def _identify_key_moments(
        self,
        scenes: list[SceneChange],
        motion: list[MotionSegment],
        duration: float,
    ) -> list[KeyMoment]:
        """
        Identify the most interesting moments for editing decisions.

        Key moments are:
          - Scene changes (natural cut points)
          - Motion peaks (action moments)
          - Stillness after motion (dramatic pauses)
        """
        moments = []

        # All scene changes are key moments
        for sc in scenes:
            moments.append(KeyMoment(
                timestamp=sc.timestamp,
                type="scene_change",
                score=sc.score,
            ))

        # Find motion peaks (local maxima)
        if len(motion) >= 3:
            for i in range(1, len(motion) - 1):
                prev_i = motion[i - 1].intensity
                curr_i = motion[i].intensity
                next_i = motion[i + 1].intensity

                # Peak: higher than neighbors and above average
                if curr_i > prev_i and curr_i > next_i and curr_i > 0.6:
                    midpoint = (motion[i].start + motion[i].end) / 2
                    moments.append(KeyMoment(
                        timestamp=midpoint,
                        type="motion_peak",
                        score=curr_i,
                    ))

                # Stillness after motion: sudden drop
                if prev_i > 0.5 and curr_i < 0.2:
                    moments.append(KeyMoment(
                        timestamp=motion[i].start,
                        type="stillness",
                        score=0.7,
                    ))

        # Find highest motion segment overall
        if motion:
            max_seg = max(motion, key=lambda m: m.intensity)
            if max_seg.intensity > 0.7:
                midpoint = (max_seg.start + max_seg.end) / 2
                # Avoid duplicates
                if not any(abs(m.timestamp - midpoint) < 0.5 for m in moments):
                    moments.append(KeyMoment(
                        timestamp=midpoint,
                        type="high_motion",
                        score=max_seg.intensity,
                    ))

        # Sort by timestamp
        moments.sort(key=lambda m: m.timestamp)

        # Limit to top 20 moments to avoid overwhelming the AI
        if len(moments) > 20:
            moments.sort(key=lambda m: m.score, reverse=True)
            moments = moments[:20]
            moments.sort(key=lambda m: m.timestamp)

        return moments

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
