"""
Celery tasks for async video processing.
Used when rendering is offloaded to a background worker.

Note: Celery is optional. The main API renders synchronously by default.
Only activate Celery for production deployments that need async rendering.
"""

import os
import time

from celery import Celery
from loguru import logger


def _get_celery_app() -> Celery:
    """Create Celery app lazily to avoid import-time config dependency."""
    from backend.config import get_settings

    settings = get_settings()
    app = Celery(
        "ai_video_editor",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_time_limit=1800,  # 30 min max per task
        task_soft_time_limit=1500,  # Soft limit: 25 min
    )
    return app


celery_app = _get_celery_app()


@celery_app.task(bind=True, name="render_video_task")
def render_video_task(
    self,
    project_id: str,
    edit_plan: dict,
    source_videos: dict,
    output_path: str,
    render_config: dict | None = None,
) -> dict:
    """
    Celery task for async video rendering.

    Args:
        project_id: Project identifier
        edit_plan: The edit plan JSON from Gemini
        source_videos: Mapping of clip_id -> file_path
        output_path: Where to save the output
        render_config: Optional render configuration dict

    Returns:
        Dict with render results (path, size, time)
    """
    from backend.config import get_settings
    from backend.engine.renderer import RenderConfig, VideoRenderer

    settings = get_settings()

    logger.info(f"[Task] Starting render for project {project_id}")
    self.update_state(state="RENDERING", meta={"project_id": project_id, "progress": 0})

    start_time = time.time()
    temp_dir = os.path.join(settings.temp_dir, f"task_{project_id}")
    renderer = VideoRenderer(
        ffmpeg_path=settings.ffmpeg_path,
        ffprobe_path=settings.ffprobe_path,
        temp_dir=temp_dir,
    )

    try:
        # Build render config
        config = RenderConfig()
        if render_config:
            config.width = render_config.get("width", config.width)
            config.height = render_config.get("height", config.height)
            config.fps = render_config.get("fps", config.fps)
            config.codec = render_config.get("codec", config.codec)
            config.preset = render_config.get("preset", config.preset)
            config.crf = render_config.get("crf", config.crf)

        # Render
        renderer.render(
            edit_plan=edit_plan,
            source_videos=source_videos,
            output_path=output_path,
            config=config,
        )

        render_time = time.time() - start_time
        output_size = os.path.getsize(output_path) / (1024 * 1024)

        result = {
            "project_id": project_id,
            "status": "completed",
            "output_path": output_path,
            "output_size_mb": round(output_size, 2),
            "render_time": round(render_time, 2),
        }

        logger.info(f"[Task] Render complete for {project_id}: {output_size:.1f} MB in {render_time:.1f}s")
        return result

    except Exception as e:
        logger.error(f"[Task] Render failed for {project_id}: {e}")
        self.update_state(state="FAILED", meta={"error": str(e)})
        raise
    finally:
        # ALWAYS cleanup temp files
        renderer.cleanup()
