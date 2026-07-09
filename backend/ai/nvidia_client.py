"""
NVIDIA NIM Agentic Video Analyzer.

Uses OpenAI-compatible API with multimodal models (Qwen 3.5-397B, etc.)
that understand images. Since NIM doesn't support native video upload,
we extract key frames and send them as images.

Agentic flow (mirrors Gemini's approach):
  Step 1: Extract frames from all clips
  Step 2: Analyze each clip individually (frames + prompt)
  Step 3: Generate final edit plan from all analyses
"""

import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
from loguru import logger

from backend.ai.base import AIProvider, AnalysisRequest, AnalysisResult


class NvidiaProvider(AIProvider):
    """
    Agentic NVIDIA NIM provider using OpenAI-compatible API.

    Multi-step:
      1. Extract 6 frames per clip (evenly spaced)
      2. For each clip: send frames + "analyze this clip" → get JSON
      3. Send all analyses + creative params → get final edit plan
    """

    BASE_URL = "https://integrate.api.nvidia.com/v1"

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        system_prompt: str | None = None,
        ffprobe_path: str = "ffprobe",
        ffmpeg_path: str = "ffmpeg",
    ):
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY", "")
        self._model_name = model_name or os.getenv("NVIDIA_MODEL", "qwen/qwen3.5-397b-a17b")
        self._system_prompt = system_prompt or self._load_system_prompt()
        self.ffprobe_path = ffprobe_path
        self.ffmpeg_path = ffmpeg_path

        if self.api_key:
            logger.info(f"NvidiaProvider (agentic) initialized: {self._model_name}")

    @property
    def name(self) -> str:
        return "nvidia"

    @property
    def model_name(self) -> str:
        return self._model_name

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key != "your_nvidia_api_key_here")

    def analyze_videos(self, request: AnalysisRequest) -> AnalysisResult:
        """
        Agentic analysis via NVIDIA NIM.

        Step 1: Extract frames from all clips
        Step 2: Analyze each clip (frames → structured JSON)
        Step 3: Generate edit plan from all clip analyses
        """
        start_time = time.time()
        total_tokens_in = 0
        total_tokens_out = 0

        # ── Step 1: Extract frames from all clips ──
        logger.info(f"[NVIDIA Agent] Step 1: Extracting frames from {len(request.video_paths)} clips...")
        clips_frames = {}
        for path in request.video_paths:
            if os.path.exists(path):
                filename = Path(path).name
                frames = self._extract_frames(path, max_frames=6)
                clips_frames[filename] = frames
                logger.info(f"  {filename}: {len(frames)} frames extracted")

        # ── Step 2: Analyze each clip ──
        logger.info(f"[NVIDIA Agent] Step 2: Analyzing clips individually...")
        clip_analyses = []

        for filename, frames in clips_frames.items():
            logger.info(f"  Analyzing {filename}...")
            analysis, tokens = self._analyze_single_clip(filename, frames)
            clip_analyses.append(analysis)
            total_tokens_in += tokens.get("input", 0)
            total_tokens_out += tokens.get("output", 0)

        logger.info(f"[NVIDIA Agent] Step 2 complete: {len(clip_analyses)} clips")

        # ── Step 3: Generate edit plan ──
        logger.info(f"[NVIDIA Agent] Step 3: Generating edit plan...")
        edit_plan, tokens = self._generate_edit_plan(clip_analyses, request)
        total_tokens_in += tokens.get("input", 0)
        total_tokens_out += tokens.get("output", 0)

        processing_time = time.time() - start_time
        logger.info(
            f"[NVIDIA Agent] Complete in {processing_time:.1f}s "
            f"(tokens: {total_tokens_in} in / {total_tokens_out} out)"
        )

        return AnalysisResult(
            edit_plan=edit_plan,
            raw_response=json.dumps(edit_plan, ensure_ascii=False),
            model_used=self._model_name,
            provider="nvidia",
            tokens_input=total_tokens_in,
            tokens_output=total_tokens_out,
            processing_time=processing_time,
        )

    def _analyze_single_clip(
        self, filename: str, frames: list[dict]
    ) -> tuple[dict, dict]:
        """Analyze one clip from its frames."""
        # Build multimodal message with frames
        content = []
        for frame in frames:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{frame['base64']}"},
            })

        content.append({
            "type": "text",
            "text": f"""Estos frames son del clip "{filename}" (extraidos uniformemente).
Analiza y devuelve JSON:
{{
  "filename": "{filename}",
  "duration_seconds": <estima la duracion>,
  "scenes": [{{"start": <s>, "end": <s>, "description": "<que pasa>"}}],
  "has_dialogue": false,
  "motion_level": "<estatico/suave/medio/alto/intenso>",
  "key_moments": [{{"timestamp": <s>, "type": "<tipo>", "description": "<que>"}}],
  "best_cut_points": [<timestamps estimados donde cortar>],
  "mood": "<emocion dominante>"
}}
SOLO JSON.""",
        })

        messages = [{"role": "user", "content": content}]

        try:
            data = self._call_api(messages, max_tokens=2048, temperature=0.3)
            text = data["choices"][0]["message"]["content"]
            result = self._parse_json(text)
            result["filename"] = filename
            tokens = data.get("usage", {})
            return result, {"input": tokens.get("prompt_tokens", 0), "output": tokens.get("completion_tokens", 0)}
        except Exception as e:
            logger.warning(f"  NVIDIA clip analysis failed for {filename}: {e}")
            return {
                "filename": filename,
                "duration_seconds": 10,
                "scenes": [],
                "motion_level": "medio",
                "key_moments": [],
                "best_cut_points": [],
                "mood": "neutral",
            }, {}

    def _generate_edit_plan(
        self, clip_analyses: list[dict], request: AnalysisRequest
    ) -> tuple[dict, dict]:
        """Generate final edit plan from all clip analyses."""
        # Build summary
        clips_summary = []
        for ca in clip_analyses:
            fname = ca.get("filename", "?")
            dur = ca.get("duration_seconds", 10)
            motion = ca.get("motion_level", "medio")
            mood = ca.get("mood", "neutral")
            cuts = ca.get("best_cut_points", [])
            moments = ca.get("key_moments", [])

            line = f"- {fname} ({dur}s): motion={motion}, mood={mood}"
            if cuts:
                line += f", cortes: {cuts[:5]}"
            if moments:
                mstr = [f"{m.get('timestamp',0):.1f}s:{m.get('type','')}" for m in moments[:3]]
                line += f", momentos: [{', '.join(mstr)}]"
            clips_summary.append(line)

        prompt = f"""Genera el plan de montaje cinematografico.

PARAMETROS:
- Genero: {request.genre}
- Ritmo: {request.rhythm}
- Referencia: {request.reference or 'ninguna'}
- Tono: {request.tone or 'segun genero'}
- Duracion: {request.duration_target or 'libre'}
{f'- Instrucciones: {request.additional_instructions}' if request.additional_instructions else ''}

CLIPS ANALIZADOS:
{chr(10).join(clips_summary)}

REGLAS:
1. Usa los "best_cut_points" para timecodes precisos.
2. Puedes reusar el mismo clip en diferentes posiciones.
3. Respeta el ritmo del genero.
4. Cada entrada del timeline necesita justificacion narrativa.

RESPONDE SOLO CON JSON: objeto con "metadata" y "timeline".
Sin texto adicional, sin markdown."""

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": prompt},
        ]

        data = self._call_api(messages, max_tokens=16384, temperature=0.7)
        text = data["choices"][0]["message"]["content"]
        plan = self._parse_json(text)
        tokens = data.get("usage", {})
        return plan, {"input": tokens.get("prompt_tokens", 0), "output": tokens.get("completion_tokens", 0)}

    # ── API Call ─────────────────────────────────────────────────────────────

    def _call_api(
        self, messages: list[dict], max_tokens: int = 4096, temperature: float = 0.7
    ) -> dict:
        """Make a request to NVIDIA NIM."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self._model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        with httpx.Client(timeout=180.0) as client:
            response = client.post(
                f"{self.BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
            )

            if response.status_code != 200:
                raise RuntimeError(f"NVIDIA API {response.status_code}: {response.text[:300]}")

            return response.json()

    # ── Frame Extraction ─────────────────────────────────────────────────────

    def _extract_frames(self, video_path: str, max_frames: int = 6) -> list[dict]:
        """Extract evenly-spaced frames as base64 JPEG."""
        import shutil
        import tempfile

        duration = self._get_duration(video_path)
        if duration <= 0:
            duration = 10.0

        interval = duration / (max_frames + 1)
        timestamps = [interval * (i + 1) for i in range(max_frames)]

        frames = []
        temp_dir = tempfile.mkdtemp(prefix="nv_frames_")

        try:
            for i, ts in enumerate(timestamps):
                out_path = os.path.join(temp_dir, f"f_{i:03d}.jpg")
                cmd = [
                    self.ffmpeg_path, "-y",
                    "-ss", f"{ts:.3f}",
                    "-i", video_path,
                    "-vframes", "1",
                    "-q:v", "4",
                    "-vf", "scale=960:-2",
                    out_path,
                ]
                try:
                    subprocess.run(cmd, capture_output=True, timeout=10, check=False)
                except subprocess.TimeoutExpired:
                    continue

                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    with open(out_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    frames.append({"base64": b64, "timestamp": ts})
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return frames

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _get_duration(self, video_path: str) -> float:
        cmd = [self.ffprobe_path, "-v", "quiet", "-print_format", "json", "-show_format", video_path]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
            return float(json.loads(r.stdout)["format"]["duration"])
        except Exception:
            return 10.0

    def _parse_json(self, text: str) -> dict:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        # Repair truncated
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        ob = cleaned.count("{") - cleaned.count("}")
        ok = cleaned.count("[") - cleaned.count("]")
        if ob > 0 or ok > 0:
            cleaned = re.sub(r',\s*"[^"]*$', '', cleaned)
            cleaned += "]" * ok + "}" * ob
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
        raise ValueError(f"NVIDIA JSON parse failed: {text[:200]}")

    def _load_system_prompt(self) -> str:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "prompts", "system_prompt.md",
        )
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return "Eres un editor de video profesional. Genera planes de montaje en JSON."
