"""
Gemini Agentic Video Analyzer.

Instead of a single monolithic prompt, this module implements a multi-step
agentic loop where Gemini:
  1. Analyzes each clip individually (visual, audio, motion, dialogue)
  2. Plans the narrative structure based on all clip analyses
  3. Generates the final edit plan with precise timecodes

Each step feeds into the next, allowing the model to reason incrementally
without hitting token limits or losing context on what it observed.
"""

import json
import os
import re
import time
from pathlib import Path

# Fix SSL on Windows
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] = certifi.where()

import google.generativeai as genai
from google.generativeai.types import HarmBlockThreshold, HarmCategory
from loguru import logger

from backend.ai.base import AIProvider, AnalysisRequest, AnalysisResult


class GeminiProvider(AIProvider):
    """
    Agentic Gemini provider — multi-step video analysis.

    Flow:
      Step 1: Upload all videos to Gemini File API
      Step 2: Analyze each clip individually (get per-clip JSON summary)
      Step 3: Send all summaries + creative params → get final edit plan

    This avoids the single-shot problem where one massive prompt
    causes truncated output or confused responses.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str = "gemini-2.5-flash",
        system_prompt_path: str | None = None,
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set")

        genai.configure(api_key=self.api_key, transport="rest")

        self._model_name = model_name
        self._system_prompt = self._load_system_prompt(system_prompt_path)

        # Model for per-clip analysis (fast, structured)
        self._analysis_model = genai.GenerativeModel(
            model_name=self._model_name,
            generation_config=genai.GenerationConfig(
                temperature=0.3,
                max_output_tokens=2048,
                response_mime_type="application/json",
            ),
        )

        # Model for final edit plan generation (creative, larger output)
        self._edit_model = genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=self._system_prompt,
            safety_settings={
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
            generation_config=genai.GenerationConfig(
                temperature=0.7,
                max_output_tokens=16384,
                response_mime_type="application/json",
            ),
        )

        logger.info(f"GeminiProvider (agentic) initialized: {self._model_name}")

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model_name

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key != "your_gemini_api_key_here")

    def analyze_videos(self, request: AnalysisRequest) -> AnalysisResult:
        """
        Agentic analysis: multi-step reasoning loop.

        Step 1: Upload videos
        Step 2: Analyze each clip (scene changes, dialogue, motion, key moments)
        Step 3: Generate edit plan using clip analyses + creative parameters
        """
        start_time = time.time()
        total_tokens_in = 0
        total_tokens_out = 0

        # ── Step 1: Upload all videos ──
        logger.info(f"[Agent] Step 1: Uploading {len(request.video_paths)} clips...")
        uploaded_files = self._upload_videos(request.video_paths)
        self._wait_for_processing(uploaded_files)

        # ── Step 2: Analyze each clip individually ──
        logger.info(f"[Agent] Step 2: Analyzing clips individually...")
        clip_analyses = []

        for i, (uploaded, path) in enumerate(zip(uploaded_files, request.video_paths)):
            filename = Path(path).name
            logger.info(f"  [{i+1}/{len(uploaded_files)}] Analyzing {filename}...")

            analysis = self._analyze_single_clip(uploaded, filename)
            clip_analyses.append(analysis)

            # Track tokens
            if "tokens" in analysis:
                total_tokens_in += analysis.get("tokens", {}).get("input", 0)
                total_tokens_out += analysis.get("tokens", {}).get("output", 0)

        logger.info(f"[Agent] Step 2 complete: {len(clip_analyses)} clips analyzed")

        # ── Step 3: Generate final edit plan ──
        logger.info(f"[Agent] Step 3: Generating edit plan...")
        edit_plan, step3_tokens = self._generate_edit_plan(
            uploaded_files, clip_analyses, request
        )
        total_tokens_in += step3_tokens.get("input", 0)
        total_tokens_out += step3_tokens.get("output", 0)

        # ── Cleanup ──
        self._cleanup_uploaded_files(uploaded_files)

        processing_time = time.time() - start_time
        logger.info(
            f"[Agent] Complete in {processing_time:.1f}s "
            f"(tokens: {total_tokens_in} in / {total_tokens_out} out)"
        )

        return AnalysisResult(
            edit_plan=edit_plan,
            raw_response=json.dumps(edit_plan, ensure_ascii=False),
            model_used=self._model_name,
            provider="gemini",
            tokens_input=total_tokens_in,
            tokens_output=total_tokens_out,
            processing_time=processing_time,
        )

    def _analyze_single_clip(self, uploaded_file, filename: str) -> dict:
        """
        Step 2: Ask Gemini to analyze ONE clip in detail.

        Returns structured info about the clip: scenes, dialogue, motion,
        key moments, best cut points.
        """
        prompt = f"""Analiza este video clip "{filename}" y devuelve un JSON con:

{{
  "filename": "{filename}",
  "duration_seconds": <duracion real del clip>,
  "scenes": [
    {{"start": <s>, "end": <s>, "description": "<que pasa en esta escena>"}}
  ],
  "has_dialogue": true/false,
  "dialogue_pauses": [<timestamps donde hay pausas entre frases>],
  "has_music": true/false,
  "tempo": "<lento/medio/rapido>",
  "motion_level": "<estatico/suave/medio/alto/intenso>",
  "key_moments": [
    {{"timestamp": <s>, "type": "<accion/emocion/cambio/silencio>", "description": "<que pasa>"}}
  ],
  "best_cut_points": [<timestamps donde un corte seria natural e invisible>],
  "mood": "<emocion dominante del clip>"
}}

SOLO JSON. Sin explicaciones."""

        try:
            response = self._analysis_model.generate_content(
                [uploaded_file, prompt],
                request_options={"timeout": 60},
            )

            result = self._parse_json(response.text)
            result["filename"] = filename

            # Extract token counts
            tokens = {}
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                tokens["input"] = getattr(response.usage_metadata, "prompt_token_count", 0)
                tokens["output"] = getattr(response.usage_metadata, "candidates_token_count", 0)
            result["tokens"] = tokens

            return result

        except Exception as e:
            logger.warning(f"  Clip analysis failed for {filename}: {e}")
            return {
                "filename": filename,
                "duration_seconds": 10,
                "scenes": [],
                "has_dialogue": False,
                "motion_level": "medio",
                "key_moments": [],
                "best_cut_points": [],
                "mood": "neutral",
                "tokens": {},
            }

    def _generate_edit_plan(
        self,
        uploaded_files: list,
        clip_analyses: list[dict],
        request: AnalysisRequest,
    ) -> tuple[dict, dict]:
        """
        Step 3: Generate the final edit plan using all clip analyses.

        The model has already seen each video individually and knows:
        - Where scenes change
        - Where dialogue pauses are (safe cut points)
        - What the motion/energy level is
        - What key moments exist

        Now it combines all that knowledge into a coherent narrative edit.
        """
        # Build concise summary of what was observed
        clips_summary = []
        for ca in clip_analyses:
            fname = ca.get("filename", "?")
            dur = ca.get("duration_seconds", 10)
            motion = ca.get("motion_level", "medio")
            mood = ca.get("mood", "neutral")
            has_dial = ca.get("has_dialogue", False)
            scenes = ca.get("scenes", [])
            cuts = ca.get("best_cut_points", [])
            moments = ca.get("key_moments", [])

            summary = f"- {fname} ({dur}s): motion={motion}, mood={mood}"
            if has_dial:
                pauses = ca.get("dialogue_pauses", [])
                summary += f", DIALOGO (pausas: {pauses[:5]})"
            if cuts:
                summary += f", cortes naturales: {cuts[:5]}"
            if moments:
                moment_strs = [f"{m.get('timestamp',0):.1f}s:{m.get('type','')}" for m in moments[:4]]
                summary += f", momentos: [{', '.join(moment_strs)}]"
            if scenes:
                scene_strs = [f"{s.get('start',0):.1f}-{s.get('end',0):.1f}s" for s in scenes[:3]]
                summary += f", escenas: [{', '.join(scene_strs)}]"

            clips_summary.append(summary)

        clips_text = "\n".join(clips_summary)

        prompt = f"""Genera el plan de montaje final basandote en tu analisis previo de los clips.

PARAMETROS CREATIVOS:
- Genero: {request.genre}
- Ritmo: {request.rhythm}
- Referencia: {request.reference or 'ninguna'}
- Tono: {request.tone or 'segun genero'}
- Duracion objetivo: {request.duration_target or 'libre'}
{f'- Instrucciones: {request.additional_instructions}' if request.additional_instructions else ''}

CLIPS ANALIZADOS:
{clips_text}

REGLAS OBLIGATORIAS:
1. Los timecodes DEBEN coincidir con los "best_cut_points" o "dialogue_pauses" que encontraste. NUNCA cortes a mitad de frase.
2. Puedes usar el MISMO clip en multiples posiciones con timecodes diferentes (fragmentacion).
3. Cada corte debe tener una justificacion narrativa.
4. Respeta el ritmo del genero pedido.
5. Prioriza "key_moments" para los puntos mas importantes del montaje.

RESPONDE SOLO CON JSON: un objeto con "metadata" y "timeline"."""

        try:
            response = self._edit_model.generate_content(
                [*uploaded_files, prompt],
                request_options={"timeout": 180},
            )

            edit_plan = self._parse_json(response.text)

            tokens = {}
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                tokens["input"] = getattr(response.usage_metadata, "prompt_token_count", 0)
                tokens["output"] = getattr(response.usage_metadata, "candidates_token_count", 0)

            return edit_plan, tokens

        except Exception as e:
            logger.error(f"Edit plan generation failed: {e}")
            raise

    # ── Upload & Processing Helpers ──────────────────────────────────────────

    def _upload_videos(self, video_paths: list[str]) -> list:
        """Upload video files to Gemini File API."""
        uploaded = []
        for path in video_paths:
            if not os.path.exists(path):
                continue
            file_size = os.path.getsize(path) / (1024 * 1024)
            logger.info(f"  Uploading {Path(path).name} ({file_size:.1f} MB)...")
            try:
                f = genai.upload_file(path, display_name=Path(path).name)
                uploaded.append(f)
            except Exception as e:
                logger.error(f"  Upload failed for {path}: {e}")
                raise
        if not uploaded:
            raise ValueError("No videos uploaded successfully")
        return uploaded

    def _wait_for_processing(self, uploaded_files: list, timeout: int = 300):
        """Wait for all files to be ready."""
        for f in uploaded_files:
            elapsed = 0
            while elapsed < timeout:
                refreshed = genai.get_file(f.name)
                if refreshed.state.name == "ACTIVE":
                    break
                elif refreshed.state.name == "FAILED":
                    raise RuntimeError(f"Processing failed for {f.display_name}")
                time.sleep(3)
                elapsed += 3
            if elapsed >= timeout:
                raise TimeoutError(f"Processing timed out for {f.display_name}")
        logger.info(f"  All {len(uploaded_files)} files ready")

    def _cleanup_uploaded_files(self, files: list):
        """Delete uploaded files from Gemini."""
        for f in files:
            try:
                genai.delete_file(f.name)
            except Exception:
                pass

    # ── JSON Parsing ─────────────────────────────────────────────────────────

    def _parse_json(self, text: str) -> dict:
        """Parse JSON with multiple fallback strategies."""
        text = text.strip()

        # Direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Extract from markdown
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Find JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        # Repair truncated JSON
        repaired = self._repair_truncated_json(text)
        if repaired:
            return repaired

        raise ValueError(f"Could not parse JSON. First 200 chars: {text[:200]}")

    def _repair_truncated_json(self, text: str) -> dict | None:
        """Fix truncated JSON by closing open brackets."""
        # Remove trailing commas
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        cleaned = re.sub(r"//[^\n]*", "", cleaned)

        # Count unclosed
        open_b = cleaned.count("{") - cleaned.count("}")
        open_k = cleaned.count("[") - cleaned.count("]")

        if open_b > 0 or open_k > 0:
            # Remove incomplete trailing content
            cleaned = re.sub(r',\s*"[^"]*$', '', cleaned)
            cleaned = re.sub(r',\s*$', '', cleaned)
            cleaned += "]" * open_k + "}" * open_b

            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

        return None

    # ── System Prompt ────────────────────────────────────────────────────────

    def _load_system_prompt(self, path: str | None) -> str:
        """Load system prompt from file."""
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()

        default_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "prompts", "system_prompt.md",
        )
        if os.path.exists(default_path):
            with open(default_path, "r", encoding="utf-8") as f:
                return f.read()

        return (
            "Eres un editor de video profesional. "
            "Genera planes de montaje en JSON con las claves 'metadata' y 'timeline'."
        )
