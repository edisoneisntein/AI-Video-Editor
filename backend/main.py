"""
FastAPI application - AI Video Editor Backend.

Endpoints:
  POST /api/upload       - Upload video clips for a new project
  POST /api/analyze      - Analyze videos with Gemini and get edit plan
  POST /api/render       - Render final video from edit plan
  GET  /api/status/{id}  - Get project status
  GET  /api/download/{id} - Download rendered video
  GET  /api/edit-plan/{id} - Get the edit plan JSON
  GET  /api/projects     - List all projects
  DELETE /api/projects/{id} - Delete project

All /api/* endpoints require X-API-Key header authentication.
Health check endpoints (/, /health) are public.
"""

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import List

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.ai import AnalysisRequest, get_provider, list_providers
from backend.auth import require_api_key
from backend.config import get_settings
from backend.engine.renderer import RenderConfig, VideoRenderer
from backend.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ProjectStatus,
    ProjectStatusResponse,
    RenderRequest,
    RenderResponse,
    UploadResponse,
)
from backend.store import get_store

# ─── App Setup ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Video Editor",
    description="Gemini-powered video editing with FFmpeg rendering",
    version="1.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded. Try again later."},
    )


# CORS middleware — restricted to configured origins only
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["X-API-Key", "Content-Type"],
)


# ─── Startup ────────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    s = get_settings()
    s.ensure_directories()

    # Initialize persistent store and recover zombie projects
    store = get_store()
    store.recover_zombie_projects(timeout_seconds=600.0)

    logger.info(f"AI Video Editor API v1.1.0 starting on {s.api_host}:{s.api_port}")
    logger.info(f"Upload dir: {s.upload_dir}")
    logger.info(f"Output dir: {s.output_dir}")
    logger.info(f"Database: {s.database_url}")
    logger.info(f"CORS origins: {s.cors_origins_list}")
    logger.info(f"Auth: API key required on /api/* endpoints")


# ─── Public Health Checks ───────────────────────────────────────────────────────


@app.get("/")
async def root():
    return {"status": "running", "service": "AI Video Editor", "version": "1.1.0"}


@app.get("/health")
async def health():
    s = get_settings()
    providers = list_providers()
    any_provider = any(p["available"] for p in providers)
    return {
        "status": "healthy",
        "ai_providers": providers,
        "any_provider_available": any_provider,
        "storage_ok": os.path.isdir(s.upload_dir),
        "auth_configured": s.api_secret_key != "change_this_to_a_strong_random_secret",
    }


# ─── Upload Endpoint (Protected) ────────────────────────────────────────────────


@app.post("/api/upload", response_model=UploadResponse)
@limiter.limit("10/minute")
async def upload_videos(
    request: Request,
    videos: List[UploadFile] = File(..., description="Video files to upload"),
    _key: str = Depends(require_api_key),
):
    """
    Upload one or more video clips to create a new editing project.
    Returns a project_id to use in subsequent API calls.

    Requires X-API-Key header.
    """
    s = get_settings()

    if not videos:
        raise HTTPException(status_code=400, detail="No video files provided")

    # Validate file types
    allowed_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mts"}
    for video in videos:
        ext = Path(video.filename or "").suffix.lower()
        if ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported format: {ext}. Allowed: {', '.join(sorted(allowed_extensions))}",
            )

    # Create project
    project_id = str(uuid.uuid4())[:8]
    project_dir = os.path.join(s.upload_dir, project_id)
    os.makedirs(project_dir, exist_ok=True)

    # Save uploaded files with streaming + pre-check size
    saved_files = []
    total_size = 0

    for video in videos:
        # Sanitize filename strictly: only keep safe characters
        raw_name = video.filename or f"upload_{uuid.uuid4().hex[:6]}.mp4"
        safe_name = _sanitize_filename(raw_name)
        file_path = os.path.join(project_dir, safe_name)

        # Stream to disk in chunks, enforcing size limit
        chunk_size = 1024 * 1024  # 1 MB chunks
        file_size = 0

        try:
            with open(file_path, "wb") as f:
                while True:
                    chunk = await video.read(chunk_size)
                    if not chunk:
                        break
                    file_size += len(chunk)
                    total_size += len(chunk)

                    # Enforce limit BEFORE writing more
                    if total_size > s.max_upload_bytes:
                        f.close()
                        shutil.rmtree(project_dir, ignore_errors=True)
                        raise HTTPException(
                            status_code=413,
                            detail=f"Total upload size exceeds {s.max_upload_size_mb}MB limit",
                        )

                    f.write(chunk)
        except HTTPException:
            raise
        except Exception as e:
            shutil.rmtree(project_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"File write error: {type(e).__name__}")

        saved_files.append(safe_name)
        logger.info(f"  Saved: {safe_name} ({file_size / 1024 / 1024:.1f} MB)")

    # Store project metadata
    store = get_store()
    store.create_project(
        project_id=project_id,
        project_dir=project_dir,
        files=saved_files,
    )

    total_mb = total_size / (1024 * 1024)
    logger.info(f"Project {project_id} created: {len(saved_files)} files, {total_mb:.1f} MB")

    return UploadResponse(
        project_id=project_id,
        status=ProjectStatus.UPLOADED,
        files_uploaded=saved_files,
        total_size_mb=round(total_mb, 2),
        message=f"Uploaded {len(saved_files)} video(s) successfully. Ready for analysis.",
    )


# ─── Analyze Endpoint (Protected) ───────────────────────────────────────────────


@app.post("/api/analyze")
@limiter.limit("5/minute")
async def analyze_videos(
    request: Request,
    body: AnalyzeRequest,
    _key: str = Depends(require_api_key),
):
    """
    Start video analysis in background. Returns immediately with task_id.
    Poll GET /api/task/{task_id} for progress and result.

    Requires X-API-Key header.
    """
    from backend.background import create_task

    s = get_settings()
    store = get_store()

    # Validate project exists
    project = store.get_project(body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get video file paths
    project_dir = project["project_dir"]
    video_paths = [
        os.path.join(project_dir, f)
        for f in project["files"]
        if os.path.exists(os.path.join(project_dir, f))
    ]

    if not video_paths:
        raise HTTPException(status_code=400, detail="No video files found in project")

    # Apply template if specified
    genre = body.genre.value
    rhythm = body.rhythm.value
    reference = body.reference
    tone = body.tone
    duration_target = body.duration_target
    additional_instructions = body.additional_instructions
    provider_name = body.provider

    if body.template_id:
        from backend.templates import get_template_store
        tpl_store = get_template_store()
        template = tpl_store.get(body.template_id)
        if not template:
            raise HTTPException(status_code=404, detail=f"Template not found")
        genre = template.genre
        rhythm = template.rhythm
        reference = template.reference
        tone = template.tone
        duration_target = template.duration_target
        additional_instructions = template.additional_instructions
        provider_name = template.provider
        tpl_store.increment_use_count(body.template_id)

    # Update status
    store.update_status(body.project_id, ProjectStatus.ANALYZING)

    # Launch background task
    def _run_analysis(task_info=None):
        """Runs in background thread."""
        if task_info:
            task_info.progress = f"Connecting to AI provider ({provider_name})..."

        provider = get_provider(provider_name)

        if task_info:
            task_info.progress = f"Uploading {len(video_paths)} clips to {provider.name}..."

        analysis_req = AnalysisRequest(
            video_paths=video_paths,
            genre=genre,
            rhythm=rhythm,
            reference=reference,
            tone=tone,
            duration_target=duration_target,
            additional_instructions=additional_instructions,
        )

        result = provider.analyze_videos(analysis_req)

        if task_info:
            task_info.progress = "Saving edit plan..."

        # Persist result
        store.update_edit_plan(body.project_id, result.edit_plan)

        plan_path = os.path.join(project_dir, "edit_plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(result.edit_plan, f, indent=2, ensure_ascii=False)

        return {
            "project_id": body.project_id,
            "status": "analyzed",
            "edit_plan": result.edit_plan,
            "model_used": result.model_used,
            "provider": result.provider,
            "tokens_input": result.tokens_input,
            "tokens_output": result.tokens_output,
            "processing_time": round(result.processing_time, 2),
        }

    task_id = create_task(
        task_type="analyze",
        project_id=body.project_id,
        target=_run_analysis,
    )

    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "project_id": body.project_id,
            "status": "accepted",
            "message": "Analysis started. Poll GET /api/task/{task_id} for progress.",
            "poll_url": f"/api/task/{task_id}",
        },
    )


# ─── Render Endpoint (Protected) ────────────────────────────────────────────────


@app.post("/api/render")
@limiter.limit("3/minute")
async def render_video(
    request: Request,
    body: RenderRequest,
    _key: str = Depends(require_api_key),
):
    """
    Start video render in background. Returns immediately with task_id.
    Poll GET /api/task/{task_id} for progress and result.

    Requires X-API-Key header.
    """
    from backend.background import create_task

    s = get_settings()
    store = get_store()

    project = store.get_project(body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    edit_plan = body.edit_plan or project.get("edit_plan")
    if not edit_plan:
        raise HTTPException(status_code=400, detail="No edit plan available. Run /api/analyze first.")

    # Concurrency check
    active_renders = store.count_active_renders()
    if active_renders >= s.max_concurrent_renders:
        raise HTTPException(status_code=429, detail="Server busy. Try again later.")

    # Build source video mapping
    project_dir = project["project_dir"]
    source_videos = {}
    for filename in project["files"]:
        filepath = os.path.join(project_dir, filename)
        if os.path.exists(filepath):
            source_videos[filename] = filepath
            source_videos[Path(filename).stem] = filepath

    if not source_videos:
        raise HTTPException(status_code=400, detail="No source video files found")

    # Parse resolution
    try:
        width, height = map(int, body.resolution.split("x"))
    except ValueError:
        width, height = 1920, 1080

    render_config = RenderConfig(
        width=width, height=height, fps=body.fps,
        codec=body.codec, preset=body.preset, crf=body.crf,
    )

    output_filename = f"{body.project_id}_final.mp4"
    output_path = os.path.join(s.output_dir, output_filename)

    store.update_status(body.project_id, ProjectStatus.RENDERING)

    # Launch background task
    def _run_render(task_info=None):
        """Runs in background thread."""
        temp_dir = os.path.join(s.temp_dir, body.project_id)
        renderer = VideoRenderer(
            ffmpeg_path=s.ffmpeg_path,
            ffprobe_path=s.ffprobe_path,
            temp_dir=temp_dir,
        )

        try:
            if task_info:
                task_info.progress = "Processing clips..."

            start_time = time.time()

            renderer.render(
                edit_plan=edit_plan,
                source_videos=source_videos,
                output_path=output_path,
                config=render_config,
            )

            render_time = time.time() - start_time
            output_size = os.path.getsize(output_path) / (1024 * 1024)

            store.update_output(body.project_id, output_path, output_filename)

            return {
                "project_id": body.project_id,
                "status": "completed",
                "output_filename": output_filename,
                "output_size_mb": round(output_size, 2),
                "render_time": round(render_time, 2),
                "download_url": f"/api/download/{body.project_id}",
            }
        except Exception as e:
            store.update_status(body.project_id, ProjectStatus.FAILED)
            raise
        finally:
            renderer.cleanup()

    task_id = create_task(
        task_type="render",
        project_id=body.project_id,
        target=_run_render,
    )

    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "project_id": body.project_id,
            "status": "accepted",
            "message": "Render started. Poll GET /api/task/{task_id} for progress.",
            "poll_url": f"/api/task/{task_id}",
        },
    )


# ─── Task Polling Endpoint ───────────────────────────────────────────────────────


@app.get("/api/task/{task_id}")
async def get_task_status(
    task_id: str,
    _key: str = Depends(require_api_key),
):
    """
    Poll the status of a background task (analyze or render).
    Returns current status, progress message, and result when complete.
    """
    from backend.background import get_task

    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return JSONResponse(content=task.to_dict())


# ─── Download Endpoint (Protected) ──────────────────────────────────────────────


@app.get("/api/download/{project_id}")
async def download_video(
    project_id: str,
    _key: str = Depends(require_api_key),
):
    """Download the rendered video file. Requires X-API-Key header."""
    store = get_store()
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    output_path = project.get("output_path")
    if not output_path or not os.path.exists(output_path):
        raise HTTPException(
            status_code=404,
            detail="No rendered video available. Run /api/render first.",
        )

    filename = project.get("output_filename", f"{project_id}_final.mp4")

    return FileResponse(
        path=output_path,
        media_type="video/mp4",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─── Edit Plan Endpoint (Protected) ─────────────────────────────────────────────


@app.get("/api/edit-plan/{project_id}")
async def get_edit_plan(
    project_id: str,
    _key: str = Depends(require_api_key),
):
    """Get the stored edit plan JSON for a project. Requires X-API-Key header."""
    store = get_store()
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    edit_plan = project.get("edit_plan")
    if not edit_plan:
        raise HTTPException(
            status_code=404,
            detail="No edit plan available. Run /api/analyze first.",
        )

    return JSONResponse(content=edit_plan)


# ─── Preview Endpoint (Protected) ───────────────────────────────────────────────


@app.post("/api/preview/{project_id}")
@limiter.limit("10/minute")
async def generate_preview(
    request: Request,
    project_id: str,
    _key: str = Depends(require_api_key),
):
    """
    Generate a visual timeline preview with thumbnails.
    Returns preview data with thumbnail URLs and an HTML timeline.

    Call this AFTER /api/analyze and BEFORE /api/render to visualize
    the edit plan without spending time on a full render.

    Requires X-API-Key header.
    """
    from backend.engine.timeline_preview import TimelinePreviewGenerator

    s = get_settings()
    store = get_store()

    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    edit_plan = project.get("edit_plan")
    if not edit_plan:
        raise HTTPException(
            status_code=400,
            detail="No edit plan available. Run /api/analyze first.",
        )

    # Build source video mapping
    project_dir = project["project_dir"]
    source_videos = {}
    for filename in project["files"]:
        filepath = os.path.join(project_dir, filename)
        if os.path.exists(filepath):
            source_videos[filename] = filepath
            stem = Path(filename).stem
            source_videos[stem] = filepath

    # Generate preview
    preview_dir = os.path.join(s.output_dir, "previews")
    generator = TimelinePreviewGenerator(
        ffmpeg_path=s.ffmpeg_path,
        output_dir=preview_dir,
    )

    try:
        preview = generator.generate(
            project_id=project_id,
            edit_plan=edit_plan,
            source_videos=source_videos,
        )
        return JSONResponse(content=preview.to_dict())

    except Exception as e:
        logger.error(f"Preview generation failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {type(e).__name__}")


@app.get("/api/preview/{project_id}/{filename}")
async def serve_preview_file(
    project_id: str,
    filename: str,
    _key: str = Depends(require_api_key),
):
    """
    Serve preview assets (thumbnails and HTML timeline).
    Requires X-API-Key header.
    """
    import re as _re

    s = get_settings()

    # Sanitize filename to prevent path traversal
    if not _re.match(r'^[\w\-.]+$', filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    preview_dir = os.path.join(s.output_dir, "previews", project_id)
    file_path = os.path.join(preview_dir, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Preview file not found")

    # Determine content type
    ext = Path(filename).suffix.lower()
    content_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".html": "text/html",
        ".json": "application/json",
    }
    content_type = content_types.get(ext, "application/octet-stream")

    return FileResponse(path=file_path, media_type=content_type)


# ─── Status Endpoint (Protected) ────────────────────────────────────────────────


@app.get("/api/status/{project_id}", response_model=ProjectStatusResponse)
async def get_project_status(
    project_id: str,
    _key: str = Depends(require_api_key),
):
    """Get current status of a project. Requires X-API-Key header."""
    store = get_store()
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return ProjectStatusResponse(
        project_id=project_id,
        status=project["status"],
        files=project["files"],
        has_edit_plan=project.get("edit_plan") is not None,
        has_output=project.get("output_path") is not None
        and os.path.exists(project.get("output_path", "")),
        output_filename=project.get("output_filename"),
        message=f"Project is {project['status'].value}",
    )


# ─── List Projects (Protected) ──────────────────────────────────────────────────


@app.get("/api/projects")
async def list_projects(_key: str = Depends(require_api_key)):
    """List all projects with their status. Requires X-API-Key header."""
    store = get_store()
    all_projects = store.list_projects()
    result = []
    for proj in all_projects:
        result.append({
            "project_id": proj["project_id"],
            "status": proj["status"].value,
            "files_count": len(proj["files"]),
            "has_output": proj.get("output_path") is not None,
        })
    return {"projects": result, "total": len(result)}


# ─── Delete Project (Protected) ─────────────────────────────────────────────────


@app.delete("/api/projects/{project_id}")
async def delete_project(
    project_id: str,
    _key: str = Depends(require_api_key),
):
    """Delete a project and its files. Requires X-API-Key header."""
    store = get_store()
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Remove files
    project_dir = project.get("project_dir")
    if project_dir and os.path.exists(project_dir):
        shutil.rmtree(project_dir, ignore_errors=True)

    output_path = project.get("output_path")
    if output_path and os.path.exists(output_path):
        os.remove(output_path)

    # Remove from store
    store.delete_project(project_id)

    return {"message": f"Project {project_id} deleted", "project_id": project_id}


# ─── Template Endpoints (Protected) ─────────────────────────────────────────────


@app.get("/api/templates")
async def list_templates(_key: str = Depends(require_api_key)):
    """List all available editing templates/presets. Requires X-API-Key header."""
    from backend.templates import get_template_store

    store = get_template_store()
    templates = store.list_all()
    return {
        "templates": [t.to_dict() for t in templates],
        "total": len(templates),
    }


@app.get("/api/templates/{template_id}")
async def get_template(
    template_id: str,
    _key: str = Depends(require_api_key),
):
    """Get a specific template by ID. Requires X-API-Key header."""
    from backend.templates import get_template_store

    store = get_template_store()
    template = store.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template.to_dict()


@app.post("/api/templates")
async def create_template(
    request: Request,
    _key: str = Depends(require_api_key),
):
    """
    Create a new editing template/preset.

    Body JSON:
      name (required), description, genre, rhythm, reference, tone,
      duration_target, additional_instructions, provider, resolution, fps, crf

    Requires X-API-Key header.
    """
    from backend.templates import get_template_store

    body = await request.json()

    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Template name is required")

    store = get_template_store()

    # Check for duplicate name
    existing = store.get_by_name(name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Template '{name}' already exists")

    template = store.create(
        name=name,
        description=body.get("description", ""),
        genre=body.get("genre", "drama"),
        rhythm=body.get("rhythm", "medio"),
        reference=body.get("reference", ""),
        tone=body.get("tone", ""),
        duration_target=body.get("duration_target", ""),
        additional_instructions=body.get("additional_instructions", ""),
        provider=body.get("provider", "auto"),
        resolution=body.get("resolution", "1920x1080"),
        fps=body.get("fps", 24),
        crf=body.get("crf", 18),
    )

    return {"message": f"Template '{name}' created", "template": template.to_dict()}


@app.put("/api/templates/{template_id}")
async def update_template(
    request: Request,
    template_id: str,
    _key: str = Depends(require_api_key),
):
    """Update an existing template. Requires X-API-Key header."""
    from backend.templates import get_template_store

    store = get_template_store()
    existing = store.get(template_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Template not found")

    body = await request.json()
    updated = store.update(template_id, **body)
    return {"message": "Template updated", "template": updated.to_dict()}


@app.delete("/api/templates/{template_id}")
async def delete_template(
    template_id: str,
    _key: str = Depends(require_api_key),
):
    """Delete a template. Requires X-API-Key header."""
    from backend.templates import get_template_store

    store = get_template_store()
    deleted = store.delete(template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"message": "Template deleted", "template_id": template_id}


# ─── Export Endpoints (Protected) ────────────────────────────────────────────────


@app.get("/api/export/{project_id}/edl")
async def export_edl(
    project_id: str,
    _key: str = Depends(require_api_key),
):
    """
    Export the edit plan as a CMX3600 EDL file.
    Compatible with Premiere Pro, DaVinci Resolve, Avid, and most NLEs.

    Requires X-API-Key header.
    """
    from backend.engine.exporters import EDLExporter

    store = get_store()
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    edit_plan = project.get("edit_plan")
    if not edit_plan:
        raise HTTPException(status_code=400, detail="No edit plan. Run /api/analyze first.")

    exporter = EDLExporter()
    edl_content = exporter.export(edit_plan, project_id)

    from fastapi.responses import Response

    return Response(
        content=edl_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{project_id}_edit.edl"',
        },
    )


@app.get("/api/export/{project_id}/xml")
async def export_xml(
    project_id: str,
    _key: str = Depends(require_api_key),
):
    """
    Export the edit plan as Final Cut Pro XML.
    Compatible with Premiere Pro, DaVinci Resolve, and Final Cut Pro.

    Requires X-API-Key header.
    """
    from backend.engine.exporters import FCPXMLExporter

    store = get_store()
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    edit_plan = project.get("edit_plan")
    if not edit_plan:
        raise HTTPException(status_code=400, detail="No edit plan. Run /api/analyze first.")

    exporter = FCPXMLExporter()
    xml_content = exporter.export(edit_plan, project_id)

    from fastapi.responses import Response

    return Response(
        content=xml_content,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{project_id}_edit.xml"',
        },
    )


# ─── Helpers ────────────────────────────────────────────────────────────────────


def _sanitize_filename(filename: str) -> str:
    """
    Strictly sanitize a filename to prevent path traversal and special char issues.
    Only allows alphanumeric, hyphens, underscores, and dots.
    """
    import re

    # Extract just the filename (no directory components)
    name = Path(filename).name

    # Split into stem and suffix
    stem = Path(name).stem
    suffix = Path(name).suffix.lower()

    # Remove everything except safe characters
    safe_stem = re.sub(r"[^\w\-]", "_", stem)
    safe_stem = re.sub(r"_+", "_", safe_stem).strip("_")

    # Ensure we have something
    if not safe_stem:
        safe_stem = f"upload_{uuid.uuid4().hex[:8]}"

    # Ensure suffix is in allowed list
    allowed_suffixes = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mts"}
    if suffix not in allowed_suffixes:
        suffix = ".mp4"

    return f"{safe_stem}{suffix}"


# ─── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=s.api_host,
        port=s.api_port,
        reload=s.debug,
    )
