"""
Transition types and parsing for video timeline cuts.

Defines the TransitionType enum and Transition dataclass used by the renderer
to determine which xfade filter to apply between clips.
"""

from dataclasses import dataclass
from enum import Enum


class TransitionType(Enum):
    HARD_CUT = "hard_cut"
    CROSS_DISSOLVE = "cross_dissolve"
    DIP_TO_BLACK = "dip_to_black"
    DIP_TO_WHITE = "dip_to_white"
    J_CUT = "j_cut"
    L_CUT = "l_cut"
    WIPE_LEFT = "wipe_left"
    WIPE_RIGHT = "wipe_right"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    MATCH_CUT = "match_cut"
    FADE_IN = "fade_in"
    FADE_OUT = "fade_out"


@dataclass
class Transition:
    """
    Represents a transition between two clips in the timeline.
    """

    type: TransitionType = TransitionType.HARD_CUT
    duration: float = 0.5  # seconds
    audio_overlap: float = 0.0  # seconds of audio overlap for J/L cuts

    @classmethod
    def from_edit_plan(cls, cut_type: str, params: dict | None = None) -> "Transition":
        """Parse transition from Gemini's JSON tipo_corte_posterior field."""
        params = params or {}

        # Normalize the type string
        type_map = {
            "hard_cut": TransitionType.HARD_CUT,
            "corte_seco": TransitionType.HARD_CUT,
            "corte_duro": TransitionType.HARD_CUT,
            "cross_dissolve": TransitionType.CROSS_DISSOLVE,
            "crossdissolve": TransitionType.CROSS_DISSOLVE,
            "disolvencia": TransitionType.CROSS_DISSOLVE,
            "fundido_cruzado": TransitionType.CROSS_DISSOLVE,
            "dip_to_black": TransitionType.DIP_TO_BLACK,
            "fundido_negro": TransitionType.DIP_TO_BLACK,
            "fade_to_black": TransitionType.DIP_TO_BLACK,
            "dip_to_white": TransitionType.DIP_TO_WHITE,
            "fundido_blanco": TransitionType.DIP_TO_WHITE,
            "j_cut": TransitionType.J_CUT,
            "j-cut": TransitionType.J_CUT,
            "jcut": TransitionType.J_CUT,
            "l_cut": TransitionType.L_CUT,
            "l-cut": TransitionType.L_CUT,
            "lcut": TransitionType.L_CUT,
            "wipe_left": TransitionType.WIPE_LEFT,
            "wipe_right": TransitionType.WIPE_RIGHT,
            "wipe": TransitionType.WIPE_LEFT,
            "zoom_in": TransitionType.ZOOM_IN,
            "zoom_out": TransitionType.ZOOM_OUT,
            "match_cut": TransitionType.MATCH_CUT,
            "corte_de_continuidad": TransitionType.MATCH_CUT,
            "fade_in": TransitionType.FADE_IN,
            "fade_out": TransitionType.FADE_OUT,
        }

        normalized = cut_type.strip().lower().replace(" ", "_")
        transition_type = type_map.get(normalized, TransitionType.HARD_CUT)
        duration = float(params.get("duracion", params.get("duration", 0.5)))

        # J/L cuts have audio overlap
        audio_overlap = 0.0
        if transition_type in (TransitionType.J_CUT, TransitionType.L_CUT):
            audio_overlap = float(
                params.get("audio_overlap", params.get("solapamiento_audio", 1.0))
            )

        return cls(type=transition_type, duration=duration, audio_overlap=audio_overlap)
