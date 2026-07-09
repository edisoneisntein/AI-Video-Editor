"""
Edit plan exporters for professional NLE software.

Exports:
  - CMX3600 EDL (Edit Decision List) — universal format for Premiere, DaVinci, Avid
  - FCP XML (Final Cut Pro XML) — compatible with Premiere Pro, DaVinci Resolve, FCP

Both formats translate the AI-generated edit plan into industry-standard
timelines that can be imported directly into professional editing software
for further refinement.
"""

from pathlib import Path


class EDLExporter:
    """
    Generate CMX3600 EDL files from edit plans.

    CMX3600 is the most universal edit interchange format. Virtually every
    NLE can import it. It supports:
      - Cut points (in/out timecodes)
      - Transition types (CUT, DISSOLVE, WIPE)
      - Source clip references
      - Track assignments (V for video, A for audio)

    Limitations:
      - No color grading info (EDL is cuts-only)
      - No speed effects (some NLEs support M2 speed changes)
      - Timecodes only, no thumbnails
    """

    def __init__(self, fps: float = 24.0, title: str = "AI_VIDEO_EDIT"):
        self.fps = fps
        self.title = title

    def export(self, edit_plan: dict, project_id: str = "001") -> str:
        """
        Convert edit plan to CMX3600 EDL string.

        Args:
            edit_plan: The AI-generated edit plan with timeline
            project_id: Used in the EDL title

        Returns:
            Complete EDL file content as string
        """
        metadata = edit_plan.get("metadata", {})
        timeline = edit_plan.get("timeline", edit_plan.get("linea_temporal", []))
        title = metadata.get("titulo_montaje", f"Project_{project_id}")

        lines = []

        # EDL Header
        lines.append(f"TITLE: {self._sanitize_edl_text(title)}")
        lines.append(f"FCM: NON-DROP FRAME")
        lines.append("")

        # Track record position in the master timeline
        master_tc = 0.0  # running timecode in seconds

        for i, clip_data in enumerate(timeline):
            event_num = i + 1
            clip_id = clip_data.get("id_clip", clip_data.get("clip_id", f"CLIP_{i}"))
            tc_in = float(clip_data.get("timecode_in", 0))
            tc_out = float(clip_data.get("timecode_out", 0))
            duration = tc_out - tc_in if tc_out > tc_in else 5.0

            # Determine transition type
            transition_type = clip_data.get("tipo_corte_posterior", "hard_cut")
            transition_params = clip_data.get("parametros_transicion", {})
            transition_duration = float(transition_params.get("duracion", 0))

            edl_transition = self._map_transition(transition_type)

            # Source timecodes (in the original clip)
            src_in = self._seconds_to_tc(tc_in)
            src_out = self._seconds_to_tc(tc_out)

            # Record timecodes (in the output timeline)
            rec_in = self._seconds_to_tc(master_tc)
            rec_out = self._seconds_to_tc(master_tc + duration)

            # Reel name (clip filename without extension, max 8 chars for CMX3600)
            reel = self._make_reel_name(clip_id)

            # EDL event line format:
            # EVENT REEL TRACK TRANSITION DURATION SRC_IN SRC_OUT REC_IN REC_OUT
            if edl_transition == "C":
                # Cut — no duration field
                lines.append(
                    f"{event_num:03d}  {reel:<8s} V     C        "
                    f"{src_in} {src_out} {rec_in} {rec_out}"
                )
            else:
                # Dissolve/Wipe with duration in frames
                dur_frames = int(transition_duration * self.fps)
                lines.append(
                    f"{event_num:03d}  {reel:<8s} V     {edl_transition} {dur_frames:03d}    "
                    f"{src_in} {src_out} {rec_in} {rec_out}"
                )

            # Add audio track entry (same timecodes)
            lines.append(
                f"{event_num:03d}  {reel:<8s} A     C        "
                f"{src_in} {src_out} {rec_in} {rec_out}"
            )

            # Speed effect comment (M2 speed change)
            transform = clip_data.get("transformacion_aplicada", {})
            if transform and transform.get("tipo") in ("slow_motion", "fast_motion"):
                factor = float(transform.get("factor", 1.0))
                speed_fps = self.fps * factor
                lines.append(f"M2   {reel:<8s} {speed_fps:05.1f}      {src_in}")

            # Source clip name as comment
            lines.append(f"* FROM CLIP NAME: {clip_id}")

            # Narrative justification as comment
            justification = clip_data.get("justificacion_narrativa", "")
            if justification:
                lines.append(f"* COMMENT: {self._sanitize_edl_text(justification[:60])}")

            lines.append("")

            # Advance master timecode
            master_tc += duration

        return "\n".join(lines)

    def _map_transition(self, transition_type: str) -> str:
        """Map our transition types to EDL transition codes."""
        mapping = {
            "hard_cut": "C",
            "corte_seco": "C",
            "match_cut": "C",
            "cross_dissolve": "D",
            "disolvencia": "D",
            "fundido_cruzado": "D",
            "dip_to_black": "D",
            "dip_to_white": "D",
            "fade_in": "D",
            "fade_out": "D",
            "j_cut": "C",  # J-cut is a cut with audio offset
            "l_cut": "C",  # L-cut is a cut with audio offset
            "wipe_left": "W001",
            "wipe_right": "W002",
        }
        return mapping.get(transition_type.lower(), "C")

    def _seconds_to_tc(self, seconds: float) -> str:
        """Convert seconds to SMPTE timecode HH:MM:SS:FF."""
        total_frames = int(seconds * self.fps)
        ff = total_frames % int(self.fps)
        total_seconds = total_frames // int(self.fps)
        ss = total_seconds % 60
        total_minutes = total_seconds // 60
        mm = total_minutes % 60
        hh = total_minutes // 60
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    def _make_reel_name(self, clip_id: str) -> str:
        """Create an 8-char reel name from clip filename."""
        stem = Path(clip_id).stem
        # Remove non-alphanumeric, uppercase, truncate to 8
        clean = "".join(c for c in stem if c.isalnum() or c == "_")
        return clean[:8].upper() or "CLIP0001"

    def _sanitize_edl_text(self, text: str) -> str:
        """Remove characters that could break EDL parsing."""
        return text.replace("\n", " ").replace("\r", "").strip()


class FCPXMLExporter:
    """
    Generate Final Cut Pro XML (version 5) from edit plans.

    FCP XML is a rich format that supports:
      - Full timeline with clips, transitions, effects
      - Speed changes (timeMap)
      - Audio levels and fades
      - Source clip metadata
      - Nested sequences

    Compatible with:
      - Adobe Premiere Pro (File > Import)
      - DaVinci Resolve (File > Import Timeline > FCP XML)
      - Final Cut Pro 7 (File > Import > XML)
      - Many other NLEs
    """

    def __init__(self, fps: float = 24.0, width: int = 1920, height: int = 1080):
        self.fps = fps
        self.width = width
        self.height = height
        self.timebase = int(fps)

    def export(self, edit_plan: dict, project_id: str = "001") -> str:
        """
        Convert edit plan to FCP XML string.

        Args:
            edit_plan: The AI-generated edit plan with timeline
            project_id: Used in sequence naming

        Returns:
            Complete FCP XML file content as string
        """
        metadata = edit_plan.get("metadata", {})
        timeline = edit_plan.get("timeline", edit_plan.get("linea_temporal", []))
        title = metadata.get("titulo_montaje", f"Project_{project_id}")

        # Calculate total duration in frames
        total_duration = sum(
            float(c.get("timecode_out", 0)) - float(c.get("timecode_in", 0))
            for c in timeline
        )
        total_frames = int(total_duration * self.fps)

        # Build XML
        xml_parts = []

        # XML Header
        xml_parts.append('<?xml version="1.0" encoding="UTF-8"?>')
        xml_parts.append('<!DOCTYPE xmeml>')
        xml_parts.append('<xmeml version="5">')
        xml_parts.append('  <project>')
        xml_parts.append(f'    <name>{self._escape_xml(title)}</name>')
        xml_parts.append('    <children>')

        # Sequence
        xml_parts.append('      <sequence>')
        xml_parts.append(f'        <name>{self._escape_xml(title)}</name>')
        xml_parts.append(f'        <duration>{total_frames}</duration>')
        xml_parts.append('        <rate>')
        xml_parts.append(f'          <timebase>{self.timebase}</timebase>')
        xml_parts.append('          <ntsc>FALSE</ntsc>')
        xml_parts.append('        </rate>')

        # Timecode
        xml_parts.append('        <timecode>')
        xml_parts.append('          <rate>')
        xml_parts.append(f'            <timebase>{self.timebase}</timebase>')
        xml_parts.append('            <ntsc>FALSE</ntsc>')
        xml_parts.append('          </rate>')
        xml_parts.append('          <string>00:00:00:00</string>')
        xml_parts.append('          <frame>0</frame>')
        xml_parts.append('          <displayformat>NDF</displayformat>')
        xml_parts.append('        </timecode>')

        # Media
        xml_parts.append('        <media>')

        # Video track
        xml_parts.append('          <video>')
        xml_parts.append('            <format>')
        xml_parts.append('              <samplecharacteristics>')
        xml_parts.append(f'                <width>{self.width}</width>')
        xml_parts.append(f'                <height>{self.height}</height>')
        xml_parts.append('                <pixelaspectratio>square</pixelaspectratio>')
        xml_parts.append('                <rate>')
        xml_parts.append(f'                  <timebase>{self.timebase}</timebase>')
        xml_parts.append('                  <ntsc>FALSE</ntsc>')
        xml_parts.append('                </rate>')
        xml_parts.append('              </samplecharacteristics>')
        xml_parts.append('            </format>')
        xml_parts.append('            <track>')

        # Add each clip to video track
        rec_frame = 0
        for i, clip_data in enumerate(timeline):
            clip_xml = self._build_clip_item(clip_data, i, rec_frame)
            xml_parts.append(clip_xml)

            # Advance record position
            tc_in = float(clip_data.get("timecode_in", 0))
            tc_out = float(clip_data.get("timecode_out", 0))
            duration = tc_out - tc_in if tc_out > tc_in else 5.0

            # Add transition if not hard cut
            transition_type = clip_data.get("tipo_corte_posterior", "hard_cut")
            if transition_type not in ("hard_cut", "match_cut", "fade_out") and i < len(timeline) - 1:
                transition_xml = self._build_transition(clip_data, rec_frame + int(duration * self.fps))
                xml_parts.append(transition_xml)

            rec_frame += int(duration * self.fps)

        xml_parts.append('            </track>')
        xml_parts.append('          </video>')

        # Audio track
        xml_parts.append('          <audio>')
        xml_parts.append('            <format>')
        xml_parts.append('              <samplecharacteristics>')
        xml_parts.append('                <samplerate>48000</samplerate>')
        xml_parts.append('                <depth>16</depth>')
        xml_parts.append('              </samplecharacteristics>')
        xml_parts.append('            </format>')
        xml_parts.append('            <track>')

        # Add audio items
        rec_frame = 0
        for i, clip_data in enumerate(timeline):
            audio_xml = self._build_audio_item(clip_data, i, rec_frame)
            xml_parts.append(audio_xml)

            tc_in = float(clip_data.get("timecode_in", 0))
            tc_out = float(clip_data.get("timecode_out", 0))
            duration = tc_out - tc_in if tc_out > tc_in else 5.0
            rec_frame += int(duration * self.fps)

        xml_parts.append('            </track>')
        xml_parts.append('          </audio>')

        # Close all elements
        xml_parts.append('        </media>')
        xml_parts.append('      </sequence>')
        xml_parts.append('    </children>')
        xml_parts.append('  </project>')
        xml_parts.append('</xmeml>')

        return "\n".join(xml_parts)

    def _build_clip_item(self, clip_data: dict, index: int, rec_start_frame: int) -> str:
        """Build a <clipitem> XML element for a video track clip."""
        clip_id = clip_data.get("id_clip", clip_data.get("clip_id", f"clip_{index}"))
        tc_in = float(clip_data.get("timecode_in", 0))
        tc_out = float(clip_data.get("timecode_out", 0))
        duration = tc_out - tc_in if tc_out > tc_in else 5.0

        src_in_frames = int(tc_in * self.fps)
        src_out_frames = int(tc_out * self.fps)
        clip_duration_frames = int(duration * self.fps)
        rec_end_frame = rec_start_frame + clip_duration_frames

        name = Path(clip_id).stem

        # Speed effect
        transform = clip_data.get("transformacion_aplicada", {})
        speed_factor = float(transform.get("factor", 1.0)) if transform else 1.0
        has_speed = transform and transform.get("tipo") in ("slow_motion", "fast_motion", "reverse")

        lines = []
        lines.append(f'              <clipitem id="{name}_{index}">')
        lines.append(f'                <name>{self._escape_xml(clip_id)}</name>')
        lines.append(f'                <duration>{clip_duration_frames}</duration>')
        lines.append('                <rate>')
        lines.append(f'                  <timebase>{self.timebase}</timebase>')
        lines.append('                  <ntsc>FALSE</ntsc>')
        lines.append('                </rate>')
        lines.append(f'                <start>{rec_start_frame}</start>')
        lines.append(f'                <end>{rec_end_frame}</end>')
        lines.append(f'                <in>{src_in_frames}</in>')
        lines.append(f'                <out>{src_out_frames}</out>')

        # File reference
        lines.append(f'                <file id="file_{index}">')
        lines.append(f'                  <name>{self._escape_xml(clip_id)}</name>')
        lines.append(f'                  <pathurl>file://./{self._escape_xml(clip_id)}</pathurl>')
        lines.append('                  <media>')
        lines.append('                    <video>')
        lines.append('                      <samplecharacteristics>')
        lines.append(f'                        <width>{self.width}</width>')
        lines.append(f'                        <height>{self.height}</height>')
        lines.append('                      </samplecharacteristics>')
        lines.append('                    </video>')
        lines.append('                    <audio>')
        lines.append('                      <samplecharacteristics>')
        lines.append('                        <samplerate>48000</samplerate>')
        lines.append('                        <depth>16</depth>')
        lines.append('                      </samplecharacteristics>')
        lines.append('                    </audio>')
        lines.append('                  </media>')
        lines.append('                </file>')

        # Speed effect filter
        if has_speed and speed_factor != 1.0:
            speed_pct = speed_factor * 100
            lines.append('                <filter>')
            lines.append('                  <effect>')
            lines.append('                    <name>Time Remap</name>')
            lines.append('                    <effectid>timeremap</effectid>')
            lines.append('                    <effecttype>motion</effecttype>')
            lines.append('                    <parameter>')
            lines.append('                      <parameterid>speed</parameterid>')
            lines.append('                      <name>speed</name>')
            lines.append(f'                      <value>{speed_pct:.1f}</value>')
            lines.append('                    </parameter>')
            lines.append('                  </effect>')
            lines.append('                </filter>')

        lines.append('              </clipitem>')

        return "\n".join(lines)

    def _build_audio_item(self, clip_data: dict, index: int, rec_start_frame: int) -> str:
        """Build a <clipitem> XML element for an audio track clip."""
        clip_id = clip_data.get("id_clip", clip_data.get("clip_id", f"clip_{index}"))
        tc_in = float(clip_data.get("timecode_in", 0))
        tc_out = float(clip_data.get("timecode_out", 0))
        duration = tc_out - tc_in if tc_out > tc_in else 5.0

        src_in_frames = int(tc_in * self.fps)
        src_out_frames = int(tc_out * self.fps)
        clip_duration_frames = int(duration * self.fps)
        rec_end_frame = rec_start_frame + clip_duration_frames

        name = Path(clip_id).stem

        # Audio volume
        audio_data = clip_data.get("audio", {})
        volume = float(audio_data.get("volumen", audio_data.get("volume", 1.0)))
        volume_db = self._linear_to_db(volume)

        lines = []
        lines.append(f'              <clipitem id="{name}_audio_{index}">')
        lines.append(f'                <name>{self._escape_xml(clip_id)}</name>')
        lines.append(f'                <duration>{clip_duration_frames}</duration>')
        lines.append('                <rate>')
        lines.append(f'                  <timebase>{self.timebase}</timebase>')
        lines.append('                  <ntsc>FALSE</ntsc>')
        lines.append('                </rate>')
        lines.append(f'                <start>{rec_start_frame}</start>')
        lines.append(f'                <end>{rec_end_frame}</end>')
        lines.append(f'                <in>{src_in_frames}</in>')
        lines.append(f'                <out>{src_out_frames}</out>')
        lines.append(f'                <file id="file_{index}"/>')

        # Audio level filter
        if volume != 1.0:
            lines.append('                <filter>')
            lines.append('                  <effect>')
            lines.append('                    <name>Audio Levels</name>')
            lines.append('                    <effectid>audiolevels</effectid>')
            lines.append('                    <effecttype>audio</effecttype>')
            lines.append('                    <parameter>')
            lines.append('                      <parameterid>level</parameterid>')
            lines.append('                      <name>Level</name>')
            lines.append(f'                      <value>{volume_db:.1f}</value>')
            lines.append('                    </parameter>')
            lines.append('                  </effect>')
            lines.append('                </filter>')

        lines.append('              </clipitem>')

        return "\n".join(lines)

    def _build_transition(self, clip_data: dict, at_frame: int) -> str:
        """Build a <transitionitem> XML element."""
        transition_type = clip_data.get("tipo_corte_posterior", "cross_dissolve")
        params = clip_data.get("parametros_transicion", {})
        duration_sec = float(params.get("duracion", 0.5))
        duration_frames = int(duration_sec * self.fps)

        # Map to FCP effect names
        effect_map = {
            "cross_dissolve": ("Cross Dissolve", "crossdissolve"),
            "dip_to_black": ("Dip to Color Dissolve", "diptocolor"),
            "dip_to_white": ("Dip to Color Dissolve", "diptocolor"),
            "wipe_left": ("Wipe", "wipe"),
            "wipe_right": ("Wipe", "wipe"),
            "j_cut": ("Cross Dissolve", "crossdissolve"),
            "l_cut": ("Cross Dissolve", "crossdissolve"),
        }

        effect_name, effect_id = effect_map.get(
            transition_type, ("Cross Dissolve", "crossdissolve")
        )

        # Alignment: center the transition over the cut point
        start_frame = at_frame - (duration_frames // 2)
        end_frame = at_frame + (duration_frames // 2)

        lines = []
        lines.append('              <transitionitem>')
        lines.append(f'                <start>{start_frame}</start>')
        lines.append(f'                <end>{end_frame}</end>')
        lines.append('                <alignment>center</alignment>')
        lines.append('                <effect>')
        lines.append(f'                  <name>{effect_name}</name>')
        lines.append(f'                  <effectid>{effect_id}</effectid>')
        lines.append('                  <effecttype>transition</effecttype>')
        lines.append('                  <mediatype>video</mediatype>')
        lines.append('                </effect>')
        lines.append('              </transitionitem>')

        return "\n".join(lines)

    def _escape_xml(self, text: str) -> str:
        """Escape special XML characters."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    def _linear_to_db(self, linear: float) -> float:
        """Convert linear volume (0-1.5) to dB for FCP XML."""
        import math
        if linear <= 0:
            return -96.0
        return 20 * math.log10(linear)
