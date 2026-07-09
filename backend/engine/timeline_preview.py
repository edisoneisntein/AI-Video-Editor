"""
Timeline preview generator.

Creates a visual HTML timeline with thumbnails BEFORE rendering,
so the user can see what the final edit will look like without
spending CPU on a full FFmpeg render.

Outputs:
  - Thumbnail strip (JPEG images for each clip segment)
  - HTML file with interactive visual timeline
  - JSON summary with timing data

Uses FFmpeg to extract frames at the exact timecodes from the edit plan.
"""

import json
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class TimelineClipPreview:
    """Preview data for a single clip in the timeline."""

    position: int
    clip_id: str
    timecode_in: float
    timecode_out: float
    duration: float
    thumbnail_path: str  # path to generated JPEG
    thumbnail_url: str  # relative URL for serving
    transition: str
    transform: str
    justification: str
    color_temp: str  # "warm", "cool", "neutral"


@dataclass
class TimelinePreview:
    """Complete timeline preview with all clips."""

    project_id: str
    total_duration: float
    clip_count: int
    clips: list[TimelineClipPreview] = field(default_factory=list)
    html_path: str = ""
    html_url: str = ""

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "total_duration": round(self.total_duration, 2),
            "clip_count": self.clip_count,
            "html_url": self.html_url,
            "clips": [
                {
                    "position": c.position,
                    "clip_id": c.clip_id,
                    "timecode_in": round(c.timecode_in, 2),
                    "timecode_out": round(c.timecode_out, 2),
                    "duration": round(c.duration, 2),
                    "thumbnail_url": c.thumbnail_url,
                    "transition": c.transition,
                    "transform": c.transform,
                    "justification": c.justification,
                }
                for c in self.clips
            ],
        }


class TimelinePreviewGenerator:
    """
    Generates visual timeline previews from an edit plan.

    For each clip in the timeline:
      1. Extract a representative thumbnail at the midpoint of the segment
      2. Generate an HTML visual timeline showing all clips with:
         - Thumbnails
         - Duration bars (proportional width)
         - Transition indicators
         - Color coding for effects
         - Timecode labels
    """

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        output_dir: str = "./storage/previews",
        thumbnail_width: int = 320,
        thumbnail_height: int = 180,
    ):
        self.ffmpeg = ffmpeg_path
        self.output_dir = output_dir
        self.thumb_width = thumbnail_width
        self.thumb_height = thumbnail_height
        os.makedirs(output_dir, exist_ok=True)

    def generate(
        self,
        project_id: str,
        edit_plan: dict,
        source_videos: dict[str, str],
    ) -> TimelinePreview:
        """
        Generate a complete timeline preview.

        Args:
            project_id: Project identifier
            edit_plan: The edit plan JSON from AI analysis
            source_videos: Mapping of clip_id -> file_path

        Returns:
            TimelinePreview with thumbnails and HTML file path
        """
        # Create project preview directory
        preview_dir = os.path.join(self.output_dir, project_id)
        os.makedirs(preview_dir, exist_ok=True)

        timeline = edit_plan.get("timeline", edit_plan.get("linea_temporal", []))

        if not timeline:
            raise ValueError("Edit plan has no timeline entries")

        logger.info(f"Generating timeline preview for {project_id} ({len(timeline)} clips)")

        # Generate thumbnails and clip data
        clip_previews = []
        total_duration = 0.0

        for i, clip_data in enumerate(timeline):
            clip_id = clip_data.get("id_clip", clip_data.get("clip_id", f"clip_{i}"))
            tc_in = float(clip_data.get("timecode_in", 0))
            tc_out = float(clip_data.get("timecode_out", 0))
            duration = tc_out - tc_in if tc_out > tc_in else 5.0

            # Find source video
            source_path = source_videos.get(clip_id)
            if not source_path:
                for vid_id, vid_path in source_videos.items():
                    if Path(vid_path).stem == Path(clip_id).stem:
                        source_path = vid_path
                        break

            # Extract thumbnail at midpoint
            midpoint = tc_in + (duration / 2)
            thumb_filename = f"thumb_{i:03d}.jpg"
            thumb_path = os.path.join(preview_dir, thumb_filename)
            thumb_url = f"/api/preview/{project_id}/{thumb_filename}"

            if source_path and os.path.exists(source_path):
                self._extract_thumbnail(source_path, midpoint, thumb_path)
            else:
                self._generate_placeholder(thumb_path, clip_id)

            # Parse metadata
            transition = clip_data.get("tipo_corte_posterior", "hard_cut")
            transform_data = clip_data.get("transformacion_aplicada", {})
            transform = transform_data.get("tipo", "ninguna") if transform_data else "ninguna"

            color_data = clip_data.get("color_grading", {})
            temp = color_data.get("temperatura_color", 0) if color_data else 0
            if isinstance(temp, str):
                color_temp = temp
            else:
                color_temp = "warm" if float(temp) > 0.1 else "cool" if float(temp) < -0.1 else "neutral"

            justification = clip_data.get(
                "justificacion_narrativa",
                clip_data.get("justification", ""),
            )

            clip_previews.append(TimelineClipPreview(
                position=i + 1,
                clip_id=clip_id,
                timecode_in=tc_in,
                timecode_out=tc_out,
                duration=duration,
                thumbnail_path=thumb_path,
                thumbnail_url=thumb_url,
                transition=transition,
                transform=transform,
                justification=justification[:100],
                color_temp=color_temp,
            ))

            total_duration += duration

        # Generate HTML timeline
        html_filename = "timeline.html"
        html_path = os.path.join(preview_dir, html_filename)
        html_url = f"/api/preview/{project_id}/{html_filename}"

        self._generate_html(
            clip_previews, edit_plan, total_duration, html_path, project_id
        )

        preview = TimelinePreview(
            project_id=project_id,
            total_duration=total_duration,
            clip_count=len(clip_previews),
            clips=clip_previews,
            html_path=html_path,
            html_url=html_url,
        )

        logger.info(f"Preview generated: {len(clip_previews)} thumbnails, {total_duration:.1f}s total")
        return preview

    def _extract_thumbnail(self, video_path: str, timestamp: float, output_path: str):
        """Extract a single frame as JPEG thumbnail."""
        cmd = [
            self.ffmpeg, "-y",
            "-ss", f"{timestamp:.3f}",
            "-i", video_path,
            "-vframes", "1",
            "-vf", f"scale={self.thumb_width}:{self.thumb_height}:force_original_aspect_ratio=decrease,"
                   f"pad={self.thumb_width}:{self.thumb_height}:(ow-iw)/2:(oh-ih)/2:color=black",
            "-q:v", "4",
            output_path,
        ]

        try:
            subprocess.run(cmd, capture_output=True, timeout=10, check=False)
        except subprocess.TimeoutExpired:
            self._generate_placeholder(output_path, "timeout")

        if not os.path.exists(output_path):
            self._generate_placeholder(output_path, "failed")

    def _generate_placeholder(self, output_path: str, label: str):
        """Generate a black placeholder thumbnail with text."""
        cmd = [
            self.ffmpeg, "-y",
            "-f", "lavfi",
            "-i", (
                f"color=c=0x1a1a2e:s={self.thumb_width}x{self.thumb_height}:d=1,"
                f"drawtext=text='{label}':fontcolor=white:fontsize=20:"
                f"x=(w-text_w)/2:y=(h-text_h)/2"
            ),
            "-vframes", "1",
            "-q:v", "4",
            output_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=10, check=False)
        except Exception:
            pass

    def _generate_html(
        self,
        clips: list[TimelineClipPreview],
        edit_plan: dict,
        total_duration: float,
        output_path: str,
        project_id: str,
    ):
        """Generate a self-contained HTML timeline visualization."""
        metadata = edit_plan.get("metadata", {})
        title = metadata.get("titulo_montaje", f"Project {project_id}")
        genre = metadata.get("genero", "")
        rhythm = metadata.get("ritmo_general", "")
        notes = metadata.get("notas_director", "")

        # Build clip HTML blocks
        clip_blocks = []
        for clip in clips:
            # Width proportional to duration
            width_pct = max(5, (clip.duration / total_duration) * 100)

            # Color based on temperature
            border_color = {
                "warm": "#ff6b35",
                "cool": "#4ecdc4",
                "neutral": "#95a5a6",
            }.get(clip.color_temp, "#95a5a6")

            # Transition icon
            transition_icons = {
                "hard_cut": "✂️",
                "cross_dissolve": "🔀",
                "dip_to_black": "⬛",
                "dip_to_white": "⬜",
                "j_cut": "🔊→",
                "l_cut": "←🔊",
                "wipe_left": "◀️",
                "wipe_right": "▶️",
                "fade_out": "🌑",
                "fade_in": "🌕",
                "match_cut": "🎯",
            }
            t_icon = transition_icons.get(clip.transition, "✂️")

            # Effect badge
            effect_badge = ""
            if clip.transform != "ninguna":
                effect_map = {
                    "slow_motion": "🐌 Slow",
                    "fast_motion": "⚡ Fast",
                    "reverse": "⏪ Rev",
                    "freeze_frame": "❄️ Freeze",
                }
                effect_badge = f'<span class="effect-badge">{effect_map.get(clip.transform, clip.transform)}</span>'

            clip_blocks.append(f"""
            <div class="clip" style="width:{width_pct:.1f}%; border-top: 3px solid {border_color};">
                <img src="{clip.thumbnail_url}" alt="{clip.clip_id}" class="thumb" loading="lazy">
                <div class="clip-info">
                    <div class="clip-name">{clip.clip_id}</div>
                    <div class="clip-time">{clip.timecode_in:.1f}s → {clip.timecode_out:.1f}s ({clip.duration:.1f}s)</div>
                    <div class="clip-note">{clip.justification}</div>
                    {effect_badge}
                </div>
                <div class="transition-marker" title="{clip.transition}">{t_icon}</div>
            </div>""")

        clips_html = "\n".join(clip_blocks)

        html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Timeline Preview — {title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f0f1a;
            color: #e0e0e0;
            padding: 24px;
        }}
        .header {{
            margin-bottom: 24px;
            border-bottom: 1px solid #2a2a3e;
            padding-bottom: 16px;
        }}
        .header h1 {{
            font-size: 1.5rem;
            color: #fff;
            margin-bottom: 8px;
        }}
        .header .meta {{
            font-size: 0.85rem;
            color: #888;
        }}
        .header .meta span {{
            margin-right: 16px;
        }}
        .stats {{
            display: flex;
            gap: 24px;
            margin-bottom: 24px;
        }}
        .stat {{
            background: #1a1a2e;
            padding: 12px 20px;
            border-radius: 8px;
            text-align: center;
        }}
        .stat-value {{
            font-size: 1.4rem;
            font-weight: 700;
            color: #4ecdc4;
        }}
        .stat-label {{
            font-size: 0.75rem;
            color: #888;
            margin-top: 4px;
        }}
        .timeline-container {{
            overflow-x: auto;
            padding: 16px 0;
        }}
        .timeline {{
            display: flex;
            align-items: stretch;
            min-height: 240px;
            gap: 2px;
        }}
        .clip {{
            flex-shrink: 0;
            min-width: 120px;
            background: #1a1a2e;
            border-radius: 8px;
            padding: 8px;
            position: relative;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .clip:hover {{
            transform: translateY(-4px);
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
            z-index: 10;
        }}
        .thumb {{
            width: 100%;
            height: 100px;
            object-fit: cover;
            border-radius: 4px;
            margin-bottom: 6px;
        }}
        .clip-info {{
            padding: 4px 0;
        }}
        .clip-name {{
            font-size: 0.75rem;
            font-weight: 600;
            color: #fff;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .clip-time {{
            font-size: 0.65rem;
            color: #4ecdc4;
            margin-top: 2px;
            font-family: monospace;
        }}
        .clip-note {{
            font-size: 0.65rem;
            color: #888;
            margin-top: 4px;
            line-height: 1.3;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}
        .effect-badge {{
            display: inline-block;
            background: #2d1b69;
            color: #b388ff;
            font-size: 0.6rem;
            padding: 2px 6px;
            border-radius: 4px;
            margin-top: 4px;
        }}
        .transition-marker {{
            position: absolute;
            right: -14px;
            top: 50%;
            transform: translateY(-50%);
            background: #0f0f1a;
            border: 2px solid #2a2a3e;
            border-radius: 50%;
            width: 26px;
            height: 26px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.7rem;
            z-index: 5;
        }}
        .clip:last-child .transition-marker {{
            display: none;
        }}
        .notes {{
            margin-top: 24px;
            padding: 16px;
            background: #1a1a2e;
            border-radius: 8px;
            border-left: 3px solid #4ecdc4;
        }}
        .notes h3 {{
            font-size: 0.85rem;
            color: #4ecdc4;
            margin-bottom: 8px;
        }}
        .notes p {{
            font-size: 0.8rem;
            color: #aaa;
            line-height: 1.5;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🎬 {title}</h1>
        <div class="meta">
            <span>Genero: <strong>{genre}</strong></span>
            <span>Ritmo: <strong>{rhythm}</strong></span>
            <span>Clips: <strong>{len(clips)}</strong></span>
        </div>
    </div>

    <div class="stats">
        <div class="stat">
            <div class="stat-value">{total_duration:.1f}s</div>
            <div class="stat-label">Duracion total</div>
        </div>
        <div class="stat">
            <div class="stat-value">{len(clips)}</div>
            <div class="stat-label">Clips</div>
        </div>
        <div class="stat">
            <div class="stat-value">{total_duration / max(len(clips), 1):.1f}s</div>
            <div class="stat-label">Promedio/clip</div>
        </div>
    </div>

    <div class="timeline-container">
        <div class="timeline">
            {clips_html}
        </div>
    </div>

    {"<div class='notes'><h3>Notas del director</h3><p>" + notes + "</p></div>" if notes else ""}
</body>
</html>"""

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
