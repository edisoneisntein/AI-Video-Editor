"""
Core video rendering engine.
Takes a structured edit plan (JSON from Gemini) and renders the final video
using FFmpeg subprocess calls.
"""

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from backend.engine.filters import ColorGrading, SpeedEffect, TransformEffect
from backend.engine.transitions import Transition, TransitionType


@dataclass
class ClipSegment:
    """A single clip segment in the timeline after parsing the edit plan."""

    source_path: str
    clip_id: str
    timecode_in: float  # seconds
    timecode_out: float  # seconds
    duration: float  # computed
    color_grading: ColorGrading = field(default_factory=ColorGrading)
    speed_effect: SpeedEffect = field(default_factory=SpeedEffect)
    transform: TransformEffect = field(default_factory=TransformEffect)
    transition_out: Transition = field(default_factory=Transition)
    audio_volume: float = 1.0  # 0.0 to 1.0
    audio_fade_in: float = 0.0
    audio_fade_out: float = 0.0


@dataclass
class RenderConfig:
    """Output video configuration."""

    width: int = 1920
    height: int = 1080
    fps: int = 24
    codec: str = "libx264"
    preset: str = "slow"
    crf: int = 18
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    pixel_format: str = "yuv420p"


class VideoRenderer:
    """
    Main rendering engine that orchestrates the FFmpeg pipeline.

    Flow:
    1. Parse edit plan JSON into ClipSegments
    2. Process each clip individually (cut, speed, color, transform)
    3. Apply transitions between clips
    4. Concatenate into final output
    """

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        temp_dir: str | None = None,
    ):
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        self.temp_dir = temp_dir or tempfile.mkdtemp(prefix="ai_editor_")
        os.makedirs(self.temp_dir, exist_ok=True)

    def render(
        self,
        edit_plan: dict,
        source_videos: dict[str, str],
        output_path: str,
        config: RenderConfig | None = None,
    ) -> str:
        """
        Render the final video from an edit plan.

        Args:
            edit_plan: Parsed JSON from Gemini with timeline and metadata
            source_videos: Mapping of clip_id -> file_path
            output_path: Where to save the final rendered video
            config: Output configuration (resolution, codec, etc.)

        Returns:
            Path to the rendered output video
        """
        config = config or RenderConfig()

        logger.info(f"Starting render with {len(edit_plan.get('timeline', []))} clips")
        logger.info(f"Output: {output_path} ({config.width}x{config.height} @ {config.fps}fps)")

        # 1. Parse timeline into ClipSegments
        segments = self._parse_timeline(edit_plan, source_videos)

        if not segments:
            raise ValueError("No valid clips found in edit plan timeline")

        logger.info(f"Parsed {len(segments)} clip segments")

        # 2. Process each clip individually
        processed_clips = []
        for i, segment in enumerate(segments):
            logger.info(f"Processing clip {i + 1}/{len(segments)}: {segment.clip_id}")
            processed_path = self._process_single_clip(segment, i, config)
            processed_clips.append(processed_path)

        # 3. Assemble clips with transitions
        if len(processed_clips) == 1:
            # Single clip: just copy to output
            self._copy_to_output(processed_clips[0], output_path, config)
        else:
            # Multiple clips: apply transitions and concatenate
            self._assemble_with_transitions(processed_clips, segments, output_path, config)

        # 4. Verify output exists
        if not os.path.exists(output_path):
            raise RuntimeError(f"Render failed: output file not created at {output_path}")

        output_size = os.path.getsize(output_path) / (1024 * 1024)
        logger.info(f"Render complete: {output_path} ({output_size:.1f} MB)")

        return output_path

    def _parse_timeline(
        self, edit_plan: dict, source_videos: dict[str, str]
    ) -> list[ClipSegment]:
        """Parse the edit plan JSON into a list of ClipSegment objects."""
        segments = []
        timeline = edit_plan.get("timeline", edit_plan.get("linea_temporal", []))

        for clip_data in timeline:
            clip_id = clip_data.get("id_clip", clip_data.get("clip_id", ""))

            # Find source video path
            source_path = source_videos.get(clip_id)
            if not source_path:
                # Try matching by filename without extension
                for vid_id, vid_path in source_videos.items():
                    if Path(vid_path).stem == Path(clip_id).stem:
                        source_path = vid_path
                        break

            if not source_path or not os.path.exists(source_path):
                logger.warning(f"Source video not found for clip '{clip_id}', skipping")
                continue

            # Parse timecodes
            tc_in = self._parse_timecode(clip_data.get("timecode_in", "0"))
            tc_out = self._parse_timecode(clip_data.get("timecode_out", ""))

            # If no out timecode, use full clip duration
            if tc_out == 0.0:
                tc_out = self._get_video_duration(source_path)

            duration = tc_out - tc_in

            # Parse effects
            color = ColorGrading.from_edit_plan(clip_data.get("color_grading", {}))
            speed = SpeedEffect.from_edit_plan(clip_data.get("transformacion_aplicada", {}))
            transform = TransformEffect.from_edit_plan(clip_data.get("transformacion_espacial", {}))

            # Parse transition
            transition_data = clip_data.get("tipo_corte_posterior", "hard_cut")
            transition_params = clip_data.get("parametros_transicion", {})
            if isinstance(transition_data, dict):
                transition_params = transition_data
                transition_data = transition_data.get("tipo", "hard_cut")
            transition = Transition.from_edit_plan(str(transition_data), transition_params)

            # Parse audio
            audio_data = clip_data.get("audio", {})
            audio_volume = float(audio_data.get("volumen", audio_data.get("volume", 1.0)))
            audio_fade_in = float(audio_data.get("fade_in", 0.0))
            audio_fade_out = float(audio_data.get("fade_out", 0.0))

            segment = ClipSegment(
                source_path=source_path,
                clip_id=clip_id,
                timecode_in=tc_in,
                timecode_out=tc_out,
                duration=duration,
                color_grading=color,
                speed_effect=speed,
                transform=transform,
                transition_out=transition,
                audio_volume=audio_volume,
                audio_fade_in=audio_fade_in,
                audio_fade_out=audio_fade_out,
            )
            segments.append(segment)

        return segments

    def _process_single_clip(
        self, segment: ClipSegment, index: int, config: RenderConfig
    ) -> str:
        """
        Process a single clip: cut, apply speed, color grading, transforms.
        Returns path to processed intermediate file.
        """
        output_path = os.path.join(self.temp_dir, f"clip_{index:03d}_processed.mp4")

        # Build FFmpeg command
        cmd = [self.ffmpeg, "-y"]

        # Input with seek (fast seek before decode)
        cmd.extend(["-ss", f"{segment.timecode_in:.3f}"])
        cmd.extend(["-i", segment.source_path])
        cmd.extend(["-t", f"{segment.duration:.3f}"])

        # Build video filter chain
        video_filters = self._build_video_filters(segment, config)

        # Build audio filter chain
        audio_filters = self._build_audio_filters(segment)

        # Apply filters
        if video_filters:
            cmd.extend(["-vf", video_filters])
        if audio_filters:
            cmd.extend(["-af", audio_filters])

        # Output settings
        cmd.extend([
            "-c:v", config.codec,
            "-preset", config.preset,
            "-crf", str(config.crf),
            "-c:a", config.audio_codec,
            "-b:a", config.audio_bitrate,
            "-pix_fmt", config.pixel_format,
            "-r", str(config.fps),
            output_path,
        ])

        self._run_ffmpeg(cmd, f"Processing clip {segment.clip_id}")
        return output_path

    def _build_video_filters(self, segment: ClipSegment, config: RenderConfig) -> str:
        """Build the complete video filter chain for a clip."""
        filters = []

        # Scale to target resolution (always, to normalize all clips)
        filters.append(f"scale={config.width}:{config.height}:force_original_aspect_ratio=decrease")
        filters.append(
            f"pad={config.width}:{config.height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )

        # Speed effect (modifies PTS) — COMBINED with timestamp reset to avoid conflict
        speed_v, _ = segment.speed_effect.to_filter_string()
        if speed_v:
            # Speed filter already contains setpts=X*PTS, prepend STARTPTS reset
            # e.g., "setpts=0.5000*PTS" becomes "setpts=0.5000*(PTS-STARTPTS)"
            speed_v = speed_v.replace("*PTS", "*(PTS-STARTPTS)")
            filters.append(speed_v)
        else:
            # No speed effect: just reset timestamps
            filters.append("setpts=PTS-STARTPTS")

        # Spatial transforms (crop, rotation, flip)
        transform_str = segment.transform.to_filter_string()
        if transform_str:
            filters.append(transform_str)

        # Color grading
        color_str = segment.color_grading.to_filter_string()
        if color_str:
            filters.append(color_str)

        # Set frame rate
        filters.append(f"fps={config.fps}")

        return ",".join(filters)

    def _build_audio_filters(self, segment: ClipSegment) -> str:
        """Build audio filter chain for a clip."""
        filters = []

        # Speed effect on audio
        _, speed_a = segment.speed_effect.to_filter_string()
        if speed_a:
            filters.append(speed_a)

        # Volume
        if segment.audio_volume != 1.0:
            filters.append(f"volume={segment.audio_volume:.3f}")

        # Fade in/out
        if segment.audio_fade_in > 0:
            filters.append(f"afade=t=in:st=0:d={segment.audio_fade_in:.3f}")
        if segment.audio_fade_out > 0:
            # Calculate start of fade (needs clip duration)
            effective_duration = segment.duration / segment.speed_effect.speed_factor
            fade_start = max(0, effective_duration - segment.audio_fade_out)
            filters.append(f"afade=t=out:st={fade_start:.3f}:d={segment.audio_fade_out:.3f}")

        return ",".join(filters)

    def _assemble_with_transitions(
        self,
        processed_clips: list[str],
        segments: list[ClipSegment],
        output_path: str,
        config: RenderConfig,
    ):
        """
        Assemble all processed clips with transitions between them.
        Uses xfade for video transitions and acrossfade for audio.
        """
        # Check if all transitions are hard cuts (simpler path)
        all_hard_cuts = all(
            seg.transition_out.type in (TransitionType.HARD_CUT, TransitionType.MATCH_CUT)
            for seg in segments[:-1]  # Last clip has no outgoing transition
        )

        # J/L cuts need the filter_complex path for audio ducking
        has_audio_overlap = any(
            seg.transition_out.type in (TransitionType.J_CUT, TransitionType.L_CUT)
            for seg in segments[:-1]
        )

        if all_hard_cuts and not has_audio_overlap:
            self._concat_simple(processed_clips, output_path, config)
        else:
            self._concat_with_xfade(processed_clips, segments, output_path, config)

    def _concat_simple(
        self, clips: list[str], output_path: str, config: RenderConfig
    ):
        """Simple concatenation using concat demuxer (all hard cuts)."""
        concat_file = os.path.join(self.temp_dir, "concat_list.txt")

        with open(concat_file, "w", encoding="utf-8") as f:
            for clip_path in clips:
                # Escape single quotes in path
                safe_path = clip_path.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        cmd = [
            self.ffmpeg, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c:v", config.codec,
            "-preset", config.preset,
            "-crf", str(config.crf),
            "-c:a", config.audio_codec,
            "-b:a", config.audio_bitrate,
            "-pix_fmt", config.pixel_format,
            output_path,
        ]

        self._run_ffmpeg(cmd, "Concatenating clips (hard cuts)")

    def _concat_with_xfade(
        self,
        clips: list[str],
        segments: list[ClipSegment],
        output_path: str,
        config: RenderConfig,
    ):
        """
        Concatenation with xfade transitions between clips.
        Chains xfade filters sequentially for each pair.
        """
        # Get duration of each processed clip
        durations = [self._get_video_duration(clip) for clip in clips]

        # Build the filter_complex graph
        # Strategy: chain xfade between sequential pairs
        n_clips = len(clips)

        # Build inputs
        cmd = [self.ffmpeg, "-y"]
        for clip_path in clips:
            cmd.extend(["-i", clip_path])

        filter_parts = []
        current_video = "[0:v]"
        current_audio = "[0:a]"
        accumulated_offset = durations[0]

        for i in range(1, n_clips):
            next_video = f"[{i}:v]"
            next_audio = f"[{i}:a]"
            out_video = f"[vfade{i}]" if i < n_clips - 1 else "[vout]"
            out_audio = f"[afade{i}]" if i < n_clips - 1 else "[aout]"

            transition = segments[i - 1].transition_out
            t_duration = transition.duration

            # Calculate offset (where in the output timeline the transition starts)
            offset = accumulated_offset - t_duration

            # Video transition using xfade
            xfade_type = self._transition_to_xfade(transition.type)
            filter_parts.append(
                f"{current_video}{next_video}xfade=transition={xfade_type}"
                f":duration={t_duration:.3f}:offset={offset:.3f}{out_video}"
            )

            # Audio transition — with ducking for J/L cuts
            if transition.type in (TransitionType.J_CUT, TransitionType.L_CUT):
                # J-cut: audio B enters early, duck audio A down
                # L-cut: audio A continues, duck audio B down initially
                duck_duration = max(t_duration, transition.audio_overlap, 0.5)

                if transition.type == TransitionType.J_CUT:
                    # A ducks down with exp curve, B enters with log curve (B dominates early)
                    filter_parts.append(
                        f"{current_audio}{next_audio}acrossfade=d={duck_duration:.3f}"
                        f":c1=exp:c2=log{out_audio}"
                    )
                else:
                    # L-cut: A stays strong (log fade out), B enters softly (exp fade in)
                    filter_parts.append(
                        f"{current_audio}{next_audio}acrossfade=d={duck_duration:.3f}"
                        f":c1=log:c2=exp{out_audio}"
                    )
            else:
                # Standard symmetric crossfade for other transitions
                filter_parts.append(
                    f"{current_audio}{next_audio}acrossfade=d={t_duration:.3f}"
                    f":c1=tri:c2=tri{out_audio}"
                )

            # Update for next iteration
            current_video = out_video
            current_audio = out_audio
            # Next accumulated offset: previous + new clip duration - transition overlap
            accumulated_offset = offset + durations[i]

        filter_complex = ";\n".join(filter_parts)

        cmd.extend(["-filter_complex", filter_complex])
        cmd.extend(["-map", "[vout]", "-map", "[aout]"])
        cmd.extend([
            "-c:v", config.codec,
            "-preset", config.preset,
            "-crf", str(config.crf),
            "-c:a", config.audio_codec,
            "-b:a", config.audio_bitrate,
            "-pix_fmt", config.pixel_format,
            output_path,
        ])

        self._run_ffmpeg(cmd, "Assembling with transitions")

    def _transition_to_xfade(self, transition_type: TransitionType) -> str:
        """Map our transition types to FFmpeg xfade transition names."""
        mapping = {
            TransitionType.HARD_CUT: "fade",
            TransitionType.CROSS_DISSOLVE: "fade",
            TransitionType.DIP_TO_BLACK: "fadeblack",
            TransitionType.DIP_TO_WHITE: "fadewhite",
            TransitionType.WIPE_LEFT: "wipeleft",
            TransitionType.WIPE_RIGHT: "wiperight",
            TransitionType.ZOOM_IN: "circlecrop",
            TransitionType.ZOOM_OUT: "circleopen",
            TransitionType.J_CUT: "fade",  # Video is a fade, audio handled separately
            TransitionType.L_CUT: "fade",
            TransitionType.FADE_IN: "fade",
            TransitionType.FADE_OUT: "fade",
            TransitionType.MATCH_CUT: "fade",
        }
        return mapping.get(transition_type, "fade")

    def _copy_to_output(self, input_path: str, output_path: str, config: RenderConfig):
        """Copy a single processed clip to the output location."""
        cmd = [
            self.ffmpeg, "-y",
            "-i", input_path,
            "-c", "copy",
            output_path,
        ]
        self._run_ffmpeg(cmd, "Copying single clip to output")

    def _get_video_duration(self, video_path: str) -> float:
        """Get video duration in seconds using ffprobe."""
        cmd = [
            self.ffprobe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            video_path,
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=30
            )
            info = json.loads(result.stdout)
            return float(info["format"]["duration"])
        except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as e:
            logger.warning(f"Could not get duration for {video_path}: {e}")
            return 10.0  # Fallback duration

    def _parse_timecode(self, timecode) -> float:
        """
        Parse timecode to seconds.
        Supports: "00:01:30.500", "90.5", "1:30", 90.5
        """
        if isinstance(timecode, (int, float)):
            return float(timecode)

        if not isinstance(timecode, str) or not timecode.strip():
            return 0.0

        timecode = timecode.strip()

        # Try direct float
        try:
            return float(timecode)
        except ValueError:
            pass

        # Parse HH:MM:SS.mmm or MM:SS.mmm or MM:SS
        parts = timecode.split(":")
        try:
            if len(parts) == 3:
                h, m, s = parts
                return float(h) * 3600 + float(m) * 60 + float(s)
            elif len(parts) == 2:
                m, s = parts
                return float(m) * 60 + float(s)
        except ValueError:
            pass

        logger.warning(f"Could not parse timecode: '{timecode}', defaulting to 0.0")
        return 0.0

    def _run_ffmpeg(self, cmd: list[str], description: str):
        """Execute an FFmpeg command with error handling."""
        logger.debug(f"FFmpeg [{description}]: {' '.join(cmd[:5])}...")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 min timeout per operation
            )

            if result.returncode != 0:
                logger.error(f"FFmpeg error [{description}]:\n{result.stderr[-2000:]}")
                raise RuntimeError(
                    f"FFmpeg failed during '{description}': {result.stderr[-500:]}"
                )

        except subprocess.TimeoutExpired:
            raise RuntimeError(f"FFmpeg timed out during '{description}' (>600s)")

    def cleanup(self):
        """Remove temporary files created during rendering."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            logger.info(f"Cleaned up temp dir: {self.temp_dir}")
