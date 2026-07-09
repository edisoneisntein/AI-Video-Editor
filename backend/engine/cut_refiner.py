"""
Cut Point Refiner.

Post-processes AI-generated timecodes to find the cleanest possible frame
within a ±0.3s window around the suggested cut point.

The AI picks timecodes based on narrative logic. But the exact frame might be:
  - Mid-blink (ugly)
  - Motion-blurred (jarring)
  - Mid-gesture (unresolved)

This module evaluates frames near the cut point and selects the one with:
  - Lowest inter-frame difference (static moment = invisible cut)
  - Avoidance of speech boundaries (don't cut mid-word)
  - Preference for motion endpoints (end of gesture, not middle)
"""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger


@dataclass
class RefinedCut:
    """A cut point refined to the cleanest frame."""

    original_timecode: float  # what the AI suggested
    refined_timecode: float  # the cleaner frame we found
    delta: float  # how much we moved it (seconds)
    quality_score: float  # 0.0-1.0 (higher = cleaner cut)
    reason: str  # why this frame was chosen


class CutPointRefiner:
    """
    Finds the cleanest frame near a suggested cut point.

    Strategy:
      1. Extract frame-level data in a ±WINDOW around the timecode
      2. Compute inter-frame difference (lower = more static = cleaner cut)
      3. Check against speech pauses (prefer cutting in silence)
      4. Return the optimal timecode within the window

    Uses FFmpeg's blend filter to compute frame differences without
    decoding to raw images in Python.
    """

    SEARCH_WINDOW = 0.3  # seconds before/after the suggested cut
    SAMPLE_INTERVAL = 0.033  # ~30fps granularity

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
    ):
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path

    def refine_edit_plan(
        self,
        edit_plan: dict,
        source_videos: dict[str, str],
        speech_data: list[dict] | None = None,
    ) -> dict:
        """
        Refine all cut points in an edit plan.

        Args:
            edit_plan: The AI-generated plan with timeline
            source_videos: Mapping of clip_id -> file_path
            speech_data: Speech analysis results for timing with phrases

        Returns:
            Modified edit_plan with refined timecodes
        """
        timeline = edit_plan.get("timeline", edit_plan.get("linea_temporal", []))

        if not timeline:
            return edit_plan

        logger.info(f"Refining {len(timeline)} cut points...")

        refined_timeline = []
        total_delta = 0.0

        for i, clip_data in enumerate(timeline):
            clip_id = clip_data.get("id_clip", clip_data.get("clip_id", ""))
            source_path = source_videos.get(clip_id)

            if not source_path:
                for vid_id, vid_path in source_videos.items():
                    if Path(vid_path).stem == Path(clip_id).stem:
                        source_path = vid_path
                        break

            if not source_path or not os.path.exists(source_path):
                refined_timeline.append(clip_data)
                continue

            # Get speech pauses for this clip
            clip_speech_pauses = self._get_speech_pauses_for_clip(
                clip_id, speech_data
            )

            # Refine timecode_in
            tc_in = float(clip_data.get("timecode_in", 0))
            if tc_in > self.SEARCH_WINDOW:
                refined_in = self._refine_single_cut(
                    source_path, tc_in, clip_speech_pauses
                )
                total_delta += abs(refined_in.delta)
                clip_data = dict(clip_data)
                clip_data["timecode_in"] = refined_in.refined_timecode
            
            # Refine timecode_out
            tc_out = float(clip_data.get("timecode_out", 0))
            duration = self._get_duration(source_path)
            if tc_out > 0 and tc_out < duration - self.SEARCH_WINDOW:
                refined_out = self._refine_single_cut(
                    source_path, tc_out, clip_speech_pauses
                )
                total_delta += abs(refined_out.delta)
                clip_data = dict(clip_data)
                clip_data["timecode_out"] = refined_out.refined_timecode

            refined_timeline.append(clip_data)

        # Update edit plan
        result = dict(edit_plan)
        if "timeline" in result:
            result["timeline"] = refined_timeline
        else:
            result["linea_temporal"] = refined_timeline

        logger.info(
            f"Cut refinement complete: avg delta={total_delta / max(len(timeline) * 2, 1):.3f}s"
        )

        return result

    def _refine_single_cut(
        self,
        video_path: str,
        timecode: float,
        speech_pauses: list[dict],
    ) -> RefinedCut:
        """
        Find the cleanest frame within ±WINDOW of the timecode.

        Uses frame difference analysis: the frame with lowest difference
        from its neighbor is the most static (cleanest cut point).
        """
        window_start = max(0, timecode - self.SEARCH_WINDOW)
        window_end = timecode + self.SEARCH_WINDOW

        # Get frame difference scores in the window
        frame_scores = self._analyze_frame_differences(
            video_path, window_start, window_end
        )

        if not frame_scores:
            return RefinedCut(
                original_timecode=timecode,
                refined_timecode=timecode,
                delta=0.0,
                quality_score=0.5,
                reason="no_data_available",
            )

        # Score each candidate: lower frame diff = better
        best_score = -1.0
        best_ts = timecode
        best_reason = "original"

        for ts, frame_diff in frame_scores:
            # Base score: inverse of frame difference (lower diff = higher score)
            score = 1.0 - min(frame_diff, 1.0)

            # Bonus: if this timestamp falls in a speech pause
            for pause in speech_pauses:
                p_start = pause.get("start", 0)
                p_end = pause.get("end", 0)
                if p_start <= ts <= p_end:
                    score += 0.3  # Strong bonus for cutting in silence
                    break

            # Penalty: distance from original (prefer staying close)
            distance_penalty = abs(ts - timecode) / self.SEARCH_WINDOW * 0.1
            score -= distance_penalty

            if score > best_score:
                best_score = score
                best_ts = ts
                if any(p.get("start", 0) <= ts <= p.get("end", 0) for p in speech_pauses):
                    best_reason = "speech_pause"
                elif frame_diff < 0.2:
                    best_reason = "static_frame"
                else:
                    best_reason = "lowest_motion"

        return RefinedCut(
            original_timecode=timecode,
            refined_timecode=round(best_ts, 3),
            delta=round(best_ts - timecode, 3),
            quality_score=round(max(0, min(1, best_score)), 3),
            reason=best_reason,
        )

    def _analyze_frame_differences(
        self, video_path: str, start: float, end: float
    ) -> list[tuple[float, float]]:
        """
        Compute frame-to-frame difference in a time window.

        Uses FFmpeg signalstats YDIF (temporal difference) for each frame.
        Returns list of (timestamp, difference_score) normalized 0-1.
        """
        duration = end - start
        cmd = [
            self.ffmpeg,
            "-ss", f"{start:.3f}",
            "-i", video_path,
            "-t", f"{duration:.3f}",
            "-vf", "signalstats=stat=tout",
            "-an",
            "-f", "null",
            "-",
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
            )
            stderr = result.stderr
        except subprocess.TimeoutExpired:
            return []

        # Parse YDIF values (temporal difference per frame)
        import re
        ydif_values = []
        for match in re.finditer(r"YDIF:\s*([\d.]+)", stderr):
            ydif_values.append(float(match.group(1)))

        if not ydif_values:
            return []

        # Normalize
        max_ydif = max(ydif_values) if ydif_values else 1.0
        if max_ydif == 0:
            max_ydif = 1.0

        # Map to timestamps
        fps = self._get_fps(video_path)
        frame_interval = 1.0 / fps if fps > 0 else 0.033

        scores = []
        for i, ydif in enumerate(ydif_values):
            ts = start + (i * frame_interval)
            normalized = ydif / max_ydif
            scores.append((ts, normalized))

        return scores

    def _get_speech_pauses_for_clip(
        self, clip_id: str, speech_data: list[dict] | None
    ) -> list[dict]:
        """Get speech pause data for a specific clip."""
        if not speech_data:
            return []

        stem = Path(clip_id).stem
        for sd in speech_data:
            if Path(sd.get("filename", "")).stem == stem:
                return sd.get("phrase_pauses", [])

        return []

    def _get_fps(self, video_path: str) -> float:
        """Get video FPS."""
        cmd = [
            self.ffprobe, "-v", "quiet",
            "-print_format", "json",
            "-show_streams", "-select_streams", "v:0",
            video_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=10
            )
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                fps_str = streams[0].get("r_frame_rate", "24/1")
                num, den = fps_str.split("/")
                return float(num) / float(den)
        except Exception:
            pass
        return 24.0

    def _get_duration(self, video_path: str) -> float:
        """Get video duration."""
        cmd = [
            self.ffprobe, "-v", "quiet",
            "-print_format", "json", "-show_format", video_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=10
            )
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except Exception:
            return 0.0
