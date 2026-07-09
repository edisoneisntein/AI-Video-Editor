"""
Utility functions shared across the application.
"""

import hashlib
import os
import re
import subprocess
from pathlib import Path

from loguru import logger


def get_file_hash(filepath: str, algorithm: str = "md5") -> str:
    """Generate hash of a file for deduplication/verification."""
    hash_func = hashlib.new(algorithm)
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_func.update(chunk)
    return hash_func.hexdigest()


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename, removing special characters."""
    # Keep only alphanumeric, dots, hyphens, underscores
    name = Path(filename).stem
    ext = Path(filename).suffix
    safe = re.sub(r"[^\w\-.]", "_", name)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return f"{safe}{ext}"


def get_video_info(filepath: str, ffprobe_path: str = "ffprobe") -> dict:
    """
    Get detailed video metadata using ffprobe.

    Returns dict with: duration, width, height, fps, codec, audio_codec, bitrate, size_mb
    """
    cmd = [
        ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        filepath,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        import json

        data = json.loads(result.stdout)

        # Extract video stream info
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        audio_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
            {},
        )
        fmt = data.get("format", {})

        # Parse FPS from r_frame_rate (e.g., "24000/1001")
        fps = 0.0
        fps_str = video_stream.get("r_frame_rate", "0/1")
        if "/" in fps_str:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) > 0 else 0.0
        else:
            fps = float(fps_str)

        return {
            "duration": float(fmt.get("duration", 0)),
            "width": int(video_stream.get("width", 0)),
            "height": int(video_stream.get("height", 0)),
            "fps": round(fps, 3),
            "video_codec": video_stream.get("codec_name", "unknown"),
            "audio_codec": audio_stream.get("codec_name", "none"),
            "bitrate": int(fmt.get("bit_rate", 0)),
            "size_mb": round(int(fmt.get("size", 0)) / (1024 * 1024), 2),
            "format": fmt.get("format_name", "unknown"),
        }

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"ffprobe failed for {filepath}: {e}")
        return {
            "duration": 0.0,
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "video_codec": "unknown",
            "audio_codec": "unknown",
            "bitrate": 0,
            "size_mb": os.path.getsize(filepath) / (1024 * 1024) if os.path.exists(filepath) else 0,
            "format": "unknown",
        }


def check_ffmpeg_available(ffmpeg_path: str = "ffmpeg") -> bool:
    """Check if FFmpeg is installed and accessible."""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}m {secs:.0f}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"


def format_file_size(bytes_size: int) -> str:
    """Format bytes as human-readable file size."""
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f} KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.2f} GB"
