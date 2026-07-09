"""
Pydantic models for API request/response validation.
"""

from enum import Enum
from pydantic import BaseModel, Field


class ProjectStatus(str, Enum):
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"


class Genre(str, Enum):
    DRAMA = "drama"
    THRILLER = "thriller"
    HORROR = "horror"
    COMEDIA = "comedia"
    DOCUMENTAL = "documental"
    ACCION = "accion"
    ROMANCE = "romance"
    CIENCIA_FICCION = "ciencia_ficcion"
    EXPERIMENTAL = "experimental"
    MUSICAL = "musical"
    NOIR = "noir"
    WESTERN = "western"


class Rhythm(str, Enum):
    MUY_LENTO = "muy_lento"
    LENTO = "lento"
    MEDIO = "medio"
    RAPIDO = "rapido"
    MUY_RAPIDO = "muy_rapido"
    VARIABLE = "variable"


# ─── Request Models ─────────────────────────────────────────────────────────────


class AnalyzeRequest(BaseModel):
    """Request body for the /analyze endpoint."""

    project_id: str = Field(..., description="Project ID (returned from upload)")
    genre: Genre = Field(default=Genre.DRAMA, description="Genero cinematografico")
    rhythm: Rhythm = Field(default=Rhythm.MEDIO, description="Ritmo de montaje")
    reference: str = Field(
        default="", description="Referencia estetica (director, pelicula, estilo)"
    )
    tone: str = Field(default="", description="Tono emocional deseado")
    duration_target: str = Field(
        default="", description="Duracion objetivo (ej: '60s', '2min')"
    )
    additional_instructions: str = Field(
        default="", description="Instrucciones adicionales para el editor IA"
    )
    provider: str = Field(
        default="auto",
        description="AI provider to use: 'gemini', 'nvidia', or 'auto' (first available)",
    )
    template_id: str = Field(
        default="",
        description="Optional template ID — if provided, overrides genre/rhythm/reference/tone/instructions with template values",
    )


class RenderRequest(BaseModel):
    """Request body for the /render endpoint."""

    project_id: str = Field(..., description="Project ID")
    edit_plan: dict | None = Field(
        default=None,
        description="Custom edit plan JSON (if None, uses the one from analysis)",
    )
    resolution: str = Field(default="1920x1080", description="Output resolution")
    fps: int = Field(default=24, ge=12, le=60, description="Output FPS")
    codec: str = Field(default="libx264", description="Video codec")
    preset: str = Field(default="slow", description="Encoding preset")
    crf: int = Field(default=18, ge=0, le=51, description="Quality (0=lossless, 51=worst)")


# ─── Response Models ────────────────────────────────────────────────────────────


class UploadResponse(BaseModel):
    """Response from /upload endpoint."""

    project_id: str
    status: ProjectStatus
    files_uploaded: list[str]
    total_size_mb: float
    message: str


class AnalyzeResponse(BaseModel):
    """Response from /analyze endpoint."""

    project_id: str
    status: ProjectStatus
    edit_plan: dict
    model_used: str
    tokens_input: int
    tokens_output: int
    processing_time: float
    message: str


class RenderResponse(BaseModel):
    """Response from /render endpoint."""

    project_id: str
    status: ProjectStatus
    output_filename: str
    output_size_mb: float
    render_time: float
    download_url: str
    message: str


class ProjectStatusResponse(BaseModel):
    """Response from /status endpoint."""

    project_id: str
    status: ProjectStatus
    files: list[str]
    has_edit_plan: bool
    has_output: bool
    output_filename: str | None = None
    message: str


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: str
    project_id: str | None = None
