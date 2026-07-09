"""
FFmpeg filter builders for video transformations.
Generates FFmpeg filter_complex strings for color grading, speed effects,
and spatial transformations.
"""

from dataclasses import dataclass, field


@dataclass
class ColorGrading:
    """
    Color correction parameters mapped to FFmpeg eq/colorbalance filters.

    All values are normalized:
    - brightness: -1.0 to 1.0 (0 = no change)
    - contrast: -1.0 to 1.0 (0 = no change, maps to FFmpeg 0.0-2.0)
    - saturation: -1.0 to 1.0 (0 = no change, maps to FFmpeg 0.0-3.0)
    - temperature: -1.0 (cool/blue) to 1.0 (warm/orange)
    - gamma: 0.1 to 3.0 (1.0 = no change)
    - shadows: -1.0 to 1.0 (lift adjustment)
    - midtones: -1.0 to 1.0
    - highlights: -1.0 to 1.0 (gain adjustment)
    """

    brightness: float = 0.0
    contrast: float = 0.0
    saturation: float = 0.0
    temperature: float = 0.0
    gamma: float = 1.0
    shadows: float = 0.0
    midtones: float = 0.0
    highlights: float = 0.0

    def to_filter_string(self) -> str:
        """Generate FFmpeg filter chain for color grading."""
        filters = []

        # eq filter: brightness, contrast, saturation, gamma
        eq_parts = []
        if self.brightness != 0.0:
            # FFmpeg eq brightness: -1.0 to 1.0
            eq_parts.append(f"brightness={self.brightness:.3f}")
        if self.contrast != 0.0:
            # FFmpeg eq contrast: 0.0 to 2.0 (1.0 = default)
            ffmpeg_contrast = 1.0 + self.contrast
            eq_parts.append(f"contrast={ffmpeg_contrast:.3f}")
        if self.saturation != 0.0:
            # FFmpeg eq saturation: 0.0 to 3.0 (1.0 = default)
            ffmpeg_sat = 1.0 + (self.saturation * 2.0 if self.saturation > 0 else self.saturation)
            eq_parts.append(f"saturation={ffmpeg_sat:.3f}")
        if self.gamma != 1.0:
            eq_parts.append(f"gamma={self.gamma:.3f}")

        if eq_parts:
            filters.append(f"eq={':'.join(eq_parts)}")

        # colorbalance filter: shadows, midtones, highlights, temperature
        cb_parts = []
        if self.shadows != 0.0:
            cb_parts.append(f"rs={self.shadows:.3f}:gs={self.shadows:.3f}:bs={self.shadows:.3f}")
        if self.midtones != 0.0:
            cb_parts.append(f"rm={self.midtones:.3f}:gm={self.midtones:.3f}:bm={self.midtones:.3f}")
        if self.highlights != 0.0:
            cb_parts.append(f"rh={self.highlights:.3f}:gh={self.highlights:.3f}:bh={self.highlights:.3f}")
        if self.temperature != 0.0:
            # Warm = more red/green in shadows, more blue in highlights (or vice versa)
            warm = self.temperature
            cb_parts.append(f"rs={warm * 0.3:.3f}:bs={-warm * 0.3:.3f}")

        if cb_parts:
            filters.append(f"colorbalance={':'.join(cb_parts)}")

        return ",".join(filters) if filters else ""

    @classmethod
    def from_edit_plan(cls, color_data: dict) -> "ColorGrading":
        """Create ColorGrading from Gemini's JSON color_grading object."""
        if not color_data:
            return cls()

        return cls(
            brightness=_parse_adjustment(color_data.get("ajuste_exposicion", "")),
            contrast=_parse_adjustment(color_data.get("ajuste_contraste", "")),
            saturation=_parse_adjustment(color_data.get("ajuste_saturacion", "")),
            temperature=_parse_temperature(color_data.get("temperatura_color", "")),
            gamma=float(color_data.get("gamma", 1.0)),
            shadows=_parse_adjustment(color_data.get("sombras", "")),
            midtones=_parse_adjustment(color_data.get("medios", "")),
            highlights=_parse_adjustment(color_data.get("altas_luces", "")),
        )


@dataclass
class SpeedEffect:
    """
    Speed/time manipulation for a clip.

    - speed_factor: 1.0 = normal, 0.5 = half speed (slow-mo), 2.0 = double speed
    - reverse: True to play clip backwards
    - freeze_frame: If set, freeze at this timestamp (seconds) for freeze_duration seconds
    """

    speed_factor: float = 1.0
    reverse: bool = False
    freeze_frame_at: float | None = None
    freeze_duration: float = 2.0

    def to_filter_string(self, stream_label: str = "") -> tuple[str, str]:
        """
        Generate video and audio filter strings for speed effects.

        Returns:
            (video_filter, audio_filter) tuple
        """
        video_filters = []
        audio_filters = []

        # Reverse
        if self.reverse:
            video_filters.append("reverse")
            audio_filters.append("areverse")

        # Speed change
        if self.speed_factor != 1.0:
            # Video: setpts adjusts presentation timestamps
            pts_factor = 1.0 / self.speed_factor
            video_filters.append(f"setpts={pts_factor:.4f}*PTS")

            # Audio: atempo (only supports 0.5 to 2.0, chain for more)
            audio_filters.extend(_build_atempo_chain(self.speed_factor))

        # Freeze frame (handled separately in renderer as it needs split/concat)
        # We just mark it here for the renderer to handle

        video_str = ",".join(video_filters) if video_filters else ""
        audio_str = ",".join(audio_filters) if audio_filters else ""

        return video_str, audio_str

    @classmethod
    def from_edit_plan(cls, transform_data: dict) -> "SpeedEffect":
        """Create SpeedEffect from Gemini's JSON transformacion_aplicada object."""
        if not transform_data:
            return cls()

        tipo = transform_data.get("tipo", "ninguna").lower()

        if tipo == "slow_motion":
            factor = transform_data.get("factor", 0.5)
            return cls(speed_factor=float(factor))
        elif tipo == "speed_up" or tipo == "fast_motion":
            factor = transform_data.get("factor", 2.0)
            return cls(speed_factor=float(factor))
        elif tipo == "reverse":
            return cls(reverse=True)
        elif tipo == "reverse_slow":
            factor = transform_data.get("factor", 0.5)
            return cls(speed_factor=float(factor), reverse=True)
        elif tipo == "freeze_frame":
            at = float(transform_data.get("en_segundo", 0.0))
            duration = float(transform_data.get("duracion", 2.0))
            return cls(freeze_frame_at=at, freeze_duration=duration)

        return cls()


@dataclass
class TransformEffect:
    """
    Spatial transformations: crop, scale, rotation, flip.
    """

    crop: dict | None = None  # {x, y, width, height} normalized 0-1
    scale: tuple[int, int] | None = None  # (width, height) in pixels
    rotation: float = 0.0  # degrees
    flip_h: bool = False
    flip_v: bool = False
    stabilize: bool = False

    def to_filter_string(self) -> str:
        """Generate FFmpeg filter string for spatial transforms."""
        filters = []

        # Crop
        if self.crop:
            w = self.crop.get("width", 1.0)
            h = self.crop.get("height", 1.0)
            x = self.crop.get("x", 0.0)
            y = self.crop.get("y", 0.0)
            filters.append(f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}")

        # Scale
        if self.scale:
            filters.append(f"scale={self.scale[0]}:{self.scale[1]}")

        # Rotation
        if self.rotation != 0.0:
            radians = self.rotation * 3.14159265 / 180.0
            filters.append(f"rotate={radians:.6f}:fillcolor=black")

        # Flip
        if self.flip_h:
            filters.append("hflip")
        if self.flip_v:
            filters.append("vflip")

        # Video stabilization
        if self.stabilize:
            filters.append("vidstabdetect=shakiness=5:accuracy=15")

        return ",".join(filters) if filters else ""

    @classmethod
    def from_edit_plan(cls, transform_data: dict) -> "TransformEffect":
        """Create TransformEffect from Gemini's JSON."""
        if not transform_data:
            return cls()

        return cls(
            crop=transform_data.get("crop"),
            scale=_parse_scale(transform_data.get("escala")),
            rotation=float(transform_data.get("rotacion", 0.0)),
            flip_h=transform_data.get("flip_horizontal", False),
            flip_v=transform_data.get("flip_vertical", False),
            stabilize=transform_data.get("estabilizar", False),
        )


# ─── Helper Functions ───────────────────────────────────────────────────────────


def _parse_adjustment(value) -> float:
    """
    Parse color adjustment strings like 'subir 15%', 'bajar 10%', '+0.2', '-0.1'
    Returns float in range -1.0 to 1.0
    """
    if isinstance(value, (int, float)):
        return max(-1.0, min(1.0, float(value)))

    if not isinstance(value, str) or not value.strip():
        return 0.0

    value = value.strip().lower()

    # Try numeric
    try:
        return max(-1.0, min(1.0, float(value)))
    except ValueError:
        pass

    # Parse Spanish descriptors
    multiplier = 1.0
    if "bajar" in value or "reducir" in value or "menos" in value:
        multiplier = -1.0
    elif "subir" in value or "aumentar" in value or "más" in value:
        multiplier = 1.0

    # Extract percentage
    import re

    match = re.search(r"(\d+(?:\.\d+)?)\s*%?", value)
    if match:
        pct = float(match.group(1))
        # Convert percentage to -1..1 range (100% = 1.0)
        return max(-1.0, min(1.0, multiplier * pct / 100.0))

    # Qualitative descriptions
    if "mucho" in value or "fuerte" in value:
        return multiplier * 0.5
    elif "poco" in value or "ligero" in value or "sutil" in value:
        return multiplier * 0.15
    elif "medio" in value or "moderado" in value:
        return multiplier * 0.3

    return multiplier * 0.2  # Default small adjustment


def _parse_temperature(value) -> float:
    """Parse temperature: 'cálido', 'frío', numeric."""
    if isinstance(value, (int, float)):
        return max(-1.0, min(1.0, float(value)))

    if not isinstance(value, str) or not value.strip():
        return 0.0

    value = value.strip().lower()

    try:
        return max(-1.0, min(1.0, float(value)))
    except ValueError:
        pass

    if "cálido" in value or "calido" in value or "warm" in value:
        return 0.3
    elif "frío" in value or "frio" in value or "cool" in value or "cold" in value:
        return -0.3
    elif "muy cálido" in value:
        return 0.6
    elif "muy frío" in value:
        return -0.6
    elif "neutro" in value or "neutral" in value:
        return 0.0

    return 0.0


def _parse_scale(value) -> tuple[int, int] | None:
    """Parse scale: '1920x1080', [1920, 1080], None."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (int(value[0]), int(value[1]))
    if isinstance(value, str) and "x" in value:
        parts = value.split("x")
        return (int(parts[0]), int(parts[1]))
    return None


def _build_atempo_chain(speed_factor: float) -> list[str]:
    """
    Build atempo filter chain. FFmpeg atempo only supports 0.5-100.0,
    so we chain multiple for extreme slow-mo.
    """
    filters = []
    remaining = speed_factor

    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5

    while remaining > 100.0:
        filters.append("atempo=100.0")
        remaining /= 100.0

    if remaining != 1.0:
        filters.append(f"atempo={remaining:.4f}")

    return filters
