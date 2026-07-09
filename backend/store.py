"""
Persistent project store using SQLite.

Replaces the in-memory dict with a durable database that survives
server restarts. Uses aiosqlite for async operations compatible with FastAPI.
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from loguru import logger

from backend.config import get_settings
from backend.schemas import ProjectStatus


class ProjectStore:
    """
    SQLite-backed persistent store for project metadata.

    Thread-safe via sqlite3's built-in serialization.
    Each operation opens and closes its own connection for safety with
    FastAPI's async worker model.
    """

    def __init__(self, db_path: str | None = None):
        settings = get_settings()
        if db_path is None:
            # Extract path from sqlite:///./storage/projects.db
            raw = settings.database_url.replace("sqlite:///", "")
            db_path = raw

        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist."""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'uploaded',
                    project_dir TEXT NOT NULL,
                    files TEXT NOT NULL DEFAULT '[]',
                    edit_plan TEXT,
                    output_path TEXT,
                    output_filename TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_projects_status 
                ON projects(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_projects_created 
                ON projects(created_at)
            """)
            conn.commit()
        logger.info(f"ProjectStore initialized: {self.db_path}")

    @contextmanager
    def _conn(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            conn.close()

    def create_project(
        self,
        project_id: str,
        project_dir: str,
        files: list[str],
    ) -> dict:
        """Create a new project entry."""
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO projects 
                   (project_id, status, project_dir, files, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    ProjectStatus.UPLOADED.value,
                    project_dir,
                    json.dumps(files),
                    now,
                    now,
                ),
            )
            conn.commit()

        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict | None:
        """Get a project by ID. Returns None if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()

        if not row:
            return None

        return self._row_to_dict(row)

    def update_status(self, project_id: str, status: ProjectStatus):
        """Update project status."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET status = ?, updated_at = ? WHERE project_id = ?",
                (status.value, time.time(), project_id),
            )
            conn.commit()

    def update_edit_plan(self, project_id: str, edit_plan: dict):
        """Store the edit plan JSON."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET edit_plan = ?, status = ?, updated_at = ? WHERE project_id = ?",
                (json.dumps(edit_plan, ensure_ascii=False), ProjectStatus.ANALYZED.value, time.time(), project_id),
            )
            conn.commit()

    def update_output(self, project_id: str, output_path: str, output_filename: str):
        """Store render output info and mark as completed."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE projects 
                   SET output_path = ?, output_filename = ?, status = ?, updated_at = ?
                   WHERE project_id = ?""",
                (output_path, output_filename, ProjectStatus.COMPLETED.value, time.time(), project_id),
            )
            conn.commit()

    def list_projects(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """List all projects ordered by creation time (newest first)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def count_projects(self) -> int:
        """Get total project count."""
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM projects").fetchone()
        return row[0]

    def delete_project(self, project_id: str) -> bool:
        """Delete a project from the store. Returns True if deleted."""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM projects WHERE project_id = ?",
                (project_id,),
            )
            conn.commit()
        return cursor.rowcount > 0

    def recover_zombie_projects(self, timeout_seconds: float = 600.0):
        """
        Find projects stuck in ANALYZING or RENDERING state for longer
        than timeout_seconds and reset them to FAILED.
        
        Called on startup to recover from crashes.
        """
        cutoff = time.time() - timeout_seconds
        with self._conn() as conn:
            cursor = conn.execute(
                """UPDATE projects 
                   SET status = ?, updated_at = ?
                   WHERE status IN (?, ?) AND updated_at < ?""",
                (
                    ProjectStatus.FAILED.value,
                    time.time(),
                    ProjectStatus.ANALYZING.value,
                    ProjectStatus.RENDERING.value,
                    cutoff,
                ),
            )
            conn.commit()

        if cursor.rowcount > 0:
            logger.warning(f"Recovered {cursor.rowcount} zombie project(s) to FAILED state")

    def count_active_renders(self) -> int:
        """Count projects currently in RENDERING state."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM projects WHERE status = ?",
                (ProjectStatus.RENDERING.value,),
            ).fetchone()
        return row[0]

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a SQLite Row to a project dict."""
        d = dict(row)
        # Parse JSON fields
        d["files"] = json.loads(d["files"]) if d["files"] else []
        d["edit_plan"] = json.loads(d["edit_plan"]) if d["edit_plan"] else None
        # Convert status string to enum
        d["status"] = ProjectStatus(d["status"])
        return d


# Module-level singleton (initialized on first import)
_store: ProjectStore | None = None


def get_store() -> ProjectStore:
    """Get or create the singleton ProjectStore instance."""
    global _store
    if _store is None:
        _store = ProjectStore()
    return _store
