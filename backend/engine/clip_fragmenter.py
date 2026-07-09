"""
Clip Pre-Fragmenter.

Automatically segments video clips into semantically meaningful fragments
based on scene changes and motion patterns. Presents these fragments
to the AI as independent units so it can make better decisions about
reuse and placement.

Instead of telling the AI "you have clip_1.mp4 (20s)", we tell it:
  - clip_1_A (0.0-5.2s): plano general, cámara estática, motion=0.12
  - clip_1_B (5.2-12.8s): paneo derecha, motion=0.67
  - clip_1_C (12.8-20.0s): primer plano, cámara estática, motion=0.08

This eliminates timecode guessing — the AI picks whole fragments.
"""

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class Fragment:
    """A semantically distinct segment of a video clip."""

    fragment_id: str  # e.g., "clip_1_A"
    source_file: str  # original filename
    source_path: str  # full path to original
    start: float  # seconds
    end: float  # seconds
    duration: float  # seconds
    avg_motion: float  # 0.0-1.0
    has_dialogue: bool  # detected speech
    description: str  # auto-generated description

    def to_dict(self) -> dict:
        return {
            "fragment_id": self.fragment_id,
            "source_file": self.source_file,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.duration, 2),
            "avg_motion": round(self.avg_motion, 3),
            "has_dialogue": self.has_dialogue,
            "description": self.description,
        }


@dataclass
class FragmentedClip:
    """A clip broken into its semantic fragments."""

    filename: str
    path: str
    total_duration: float
    fragments: list[Fragment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "total_duration": round(self.total_duration, 2),
            "fragment_count": len(self.fragments),
            "fragments": [f.to_dict() for f in self.fragments],
        }


class ClipFragmenter:
    """
    Segments clips into fragments using scene detection results.

    Strategy:
      1. Use scene changes (from SceneDetector) as primary split points
      2. If no scene changes, split by motion intensity changes
      3. Minimum fragment duration: 1.5s (anything shorter merges with neighbor)
      4. Maximum fragment duration: 15s (longer segments get split at motion valleys)
      5. Annotate each fragment with motion level and dialogue presence
    """

    MIN_FRAGMENT_DURATION = 1.5  # seconds
    MAX_FRAGMENT_DURATION = 15.0  # seconds

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
    ):
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path

    def fragment_clips(
        self,
        video_paths: list[str],
        scene_data: list[dict] | None = None,
        audio_data: list[dict] | None = None,
    ) -> list[FragmentedClip]:
        """
        Fragment multiple clips using pre-computed scene/audio data.

        Args:
            video_paths: List of video file paths
            scene_data: Scene detection results (from SceneDetector.analyze_multiple)
            audio_data: Audio analysis results (from BeatDetector.analyze_multiple)

        Returns:
            List of FragmentedClip with fragments ready for AI consumption
        """
        results = []

        for i, path in enumerate(video_paths):
            if not os.path.exists(path):
                continue

            filename = Path(path).name
            duration = self._get_duration(path)

            # Get scene data for this clip
            clip_scene = None
            if scene_data and i < len(scene_data):
                clip_scene = scene_data[i]

            # Get audio data for this clip
            clip_audio = None
            if audio_data and i < len(audio_data):
                clip_audio = audio_data[i]

            # Determine split points
            split_points = self._compute_split_points(
                duration, clip_scene, clip_audio
            )

            # Build fragments
            fragments = self._build_fragments(
                filename, path, duration, split_points, clip_scene, clip_audio
            )

            results.append(FragmentedClip(
                filename=filename,
                path=path,
                total_duration=duration,
                fragments=fragments,
            ))

            logger.info(
                f"  {filename}: {len(fragments)} fragments "
                f"(durations: {[round(f.duration, 1) for f in fragments]})"
            )

        return results

    def _compute_split_points(
        self,
        duration: float,
        scene_data: dict | None,
        audio_data: dict | None,
    ) -> list[float]:
        """
        Determine where to split the clip.

        Priority:
          1. Scene changes (strongest signal)
          2. Motion valleys (low motion = natural pause)
          3. Uniform split if nothing else (every MAX_FRAGMENT_DURATION)
        """
        split_points = []

        # 1. Use scene changes as primary splits
        if scene_data:
            scene_changes = scene_data.get("scene_changes", [])
            for sc in scene_changes:
                ts = sc.get("timestamp", 0)
                score = sc.get("score", 0)
                # Only use confident scene changes
                if score >= 0.3 and self.MIN_FRAGMENT_DURATION < ts < (duration - self.MIN_FRAGMENT_DURATION):
                    split_points.append(ts)

        # 2. If segments are still too long, split at motion valleys
        split_points = sorted(set(split_points))
        split_points = self._enforce_max_duration(split_points, duration, scene_data)

        # 3. If no splits at all and clip is long, uniform split
        if not split_points and duration > self.MAX_FRAGMENT_DURATION:
            n_segments = int(duration / self.MAX_FRAGMENT_DURATION) + 1
            interval = duration / n_segments
            split_points = [interval * i for i in range(1, n_segments)]

        # 4. Remove splits that create too-short fragments
        split_points = self._merge_short_fragments(split_points, duration)

        return sorted(split_points)

    def _enforce_max_duration(
        self,
        split_points: list[float],
        duration: float,
        scene_data: dict | None,
    ) -> list[float]:
        """Add splits where segments exceed MAX_FRAGMENT_DURATION."""
        boundaries = [0.0] + sorted(split_points) + [duration]
        new_splits = list(split_points)

        for i in range(len(boundaries) - 1):
            seg_start = boundaries[i]
            seg_end = boundaries[i + 1]
            seg_duration = seg_end - seg_start

            if seg_duration > self.MAX_FRAGMENT_DURATION:
                # Find motion valley to split at
                valley = self._find_motion_valley(
                    seg_start, seg_end, scene_data
                )
                if valley:
                    new_splits.append(valley)
                else:
                    # Uniform split
                    midpoint = (seg_start + seg_end) / 2
                    new_splits.append(midpoint)

        return sorted(set(new_splits))

    def _find_motion_valley(
        self, start: float, end: float, scene_data: dict | None
    ) -> float | None:
        """Find the lowest-motion timestamp between start and end."""
        if not scene_data:
            return None

        motion_segments = scene_data.get("motion_segments", [])
        min_motion = 1.0
        min_ts = None

        for seg in motion_segments:
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            intensity = seg.get("intensity", 0.5)

            midpoint = (seg_start + seg_end) / 2

            if start + self.MIN_FRAGMENT_DURATION < midpoint < end - self.MIN_FRAGMENT_DURATION:
                if intensity < min_motion:
                    min_motion = intensity
                    min_ts = midpoint

        return min_ts

    def _merge_short_fragments(
        self, split_points: list[float], duration: float
    ) -> list[float]:
        """Remove split points that would create fragments shorter than MIN_FRAGMENT_DURATION."""
        if not split_points:
            return []

        boundaries = [0.0] + sorted(split_points) + [duration]
        valid_splits = []

        for i in range(1, len(boundaries) - 1):
            prev = boundaries[i - 1]
            curr = boundaries[i]
            next_b = boundaries[i + 1] if i + 1 < len(boundaries) else duration

            # Check both segments around this split point
            if (curr - prev) >= self.MIN_FRAGMENT_DURATION and (next_b - curr) >= self.MIN_FRAGMENT_DURATION:
                valid_splits.append(curr)

        return valid_splits

    def _build_fragments(
        self,
        filename: str,
        path: str,
        duration: float,
        split_points: list[float],
        scene_data: dict | None,
        audio_data: dict | None,
    ) -> list[Fragment]:
        """Build Fragment objects from split points with metadata."""
        boundaries = [0.0] + split_points + [duration]
        fragments = []
        stem = Path(filename).stem

        # Labels: A, B, C, ... Z, AA, AB, ...
        labels = self._generate_labels(len(boundaries) - 1)

        for i in range(len(boundaries) - 1):
            frag_start = boundaries[i]
            frag_end = boundaries[i + 1]
            frag_duration = frag_end - frag_start

            # Calculate average motion for this fragment
            avg_motion = self._get_avg_motion_in_range(
                frag_start, frag_end, scene_data
            )

            # Check for dialogue in this range
            has_dialogue = self._has_dialogue_in_range(
                frag_start, frag_end, audio_data
            )

            # Generate description
            description = self._describe_fragment(
                avg_motion, has_dialogue, frag_duration
            )

            fragment_id = f"{stem}_{labels[i]}"

            fragments.append(Fragment(
                fragment_id=fragment_id,
                source_file=filename,
                source_path=path,
                start=frag_start,
                end=frag_end,
                duration=frag_duration,
                avg_motion=avg_motion,
                has_dialogue=has_dialogue,
                description=description,
            ))

        return fragments

    def _get_avg_motion_in_range(
        self, start: float, end: float, scene_data: dict | None
    ) -> float:
        """Get average motion intensity for a time range."""
        if not scene_data:
            return 0.5

        motion_segments = scene_data.get("motion_segments", [])
        values = []

        for seg in motion_segments:
            seg_mid = (seg.get("start", 0) + seg.get("end", 0)) / 2
            if start <= seg_mid <= end:
                values.append(seg.get("intensity", 0.5))

        return sum(values) / len(values) if values else 0.5

    def _has_dialogue_in_range(
        self, start: float, end: float, audio_data: dict | None
    ) -> bool:
        """Check if there's likely dialogue in this range."""
        if not audio_data:
            return False

        # If audio has dialogue flag and silences that suggest speech pattern
        if not audio_data.get("has_dialogue", False):
            return False

        # Check if there are silences in this range (speech has pauses)
        silences = audio_data.get("silences", [])
        for silence in silences:
            s_start = silence.get("start", 0)
            s_end = silence.get("end", 0)
            if start <= s_start <= end or start <= s_end <= end:
                return True

        # If has_dialogue is true and energy is moderate, assume speech
        energy = audio_data.get("energy_curve", [])
        for seg in energy:
            seg_mid = (seg.get("start", 0) + seg.get("end", 0)) / 2
            if start <= seg_mid <= end:
                level = seg.get("level", 0)
                if 0.3 < level < 0.8:  # Speech range (not silence, not music peak)
                    return True

        return False

    def _describe_fragment(
        self, avg_motion: float, has_dialogue: bool, duration: float
    ) -> str:
        """Auto-generate a description for the fragment."""
        parts = []

        # Motion descriptor
        if avg_motion < 0.15:
            parts.append("estatico")
        elif avg_motion < 0.35:
            parts.append("movimiento_suave")
        elif avg_motion < 0.6:
            parts.append("movimiento_medio")
        elif avg_motion < 0.8:
            parts.append("movimiento_alto")
        else:
            parts.append("movimiento_intenso")

        # Dialogue
        if has_dialogue:
            parts.append("con_dialogo")

        # Duration category
        if duration < 3:
            parts.append("corto")
        elif duration > 10:
            parts.append("largo")

        return ", ".join(parts)

    def _generate_labels(self, count: int) -> list[str]:
        """Generate A, B, C, ... Z, AA, AB, ... labels."""
        labels = []
        for i in range(count):
            if i < 26:
                labels.append(chr(65 + i))  # A-Z
            else:
                labels.append(chr(65 + (i // 26) - 1) + chr(65 + (i % 26)))
        return labels

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
