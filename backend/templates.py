"""
Templates/Presets system for reusable editing styles.

Allows users to save their favorite editing configurations
and apply them to new projects without re-configuring each time.

A template stores: name, genre, rhythm, reference, tone, duration_target,
additional_instructions, provider preference, and render settings.
"""

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

from loguru import logger

from backend.config import get_settings


@dataclass
class Template:
    """An editing style preset."""

    template_id: str
    name: str
    description: str
    # Creative parameters
    genre: str
    rhythm: str
    reference: str
    tone: str
    duration_target: str
    additional_instructions: str
    # Provider preference
    provider: str  # "auto", "gemini", "nvidia"
    # Render settings
    resolution: str
    fps: int
    crf: int
    # Metadata
    created_at: float
    updated_at: float
    use_count: int

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "description": self.description,
            "genre": self.genre,
            "rhythm": self.rhythm,
            "reference": self.reference,
            "tone": self.tone,
            "duration_target": self.duration_target,
            "additional_instructions": self.additional_instructions,
            "provider": self.provider,
            "resolution": self.resolution,
            "fps": self.fps,
            "crf": self.crf,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "use_count": self.use_count,
        }


class TemplateStore:
    """
    SQLite-backed store for editing templates.

    Uses the same database as the project store for simplicity.
    """

    def __init__(self, db_path: str | None = None):
        settings = get_settings()
        if db_path is None:
            raw = settings.database_url.replace("sqlite:///", "")
            db_path = raw

        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        """Create templates table if it doesn't exist."""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS templates (
                    template_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    genre TEXT NOT NULL DEFAULT 'drama',
                    rhythm TEXT NOT NULL DEFAULT 'medio',
                    reference TEXT NOT NULL DEFAULT '',
                    tone TEXT NOT NULL DEFAULT '',
                    duration_target TEXT NOT NULL DEFAULT '',
                    additional_instructions TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT 'auto',
                    resolution TEXT NOT NULL DEFAULT '1920x1080',
                    fps INTEGER NOT NULL DEFAULT 24,
                    crf INTEGER NOT NULL DEFAULT 18,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_templates_name
                ON templates(name)
            """)
            conn.commit()

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

    def create(
        self,
        name: str,
        description: str = "",
        genre: str = "drama",
        rhythm: str = "medio",
        reference: str = "",
        tone: str = "",
        duration_target: str = "",
        additional_instructions: str = "",
        provider: str = "auto",
        resolution: str = "1920x1080",
        fps: int = 24,
        crf: int = 18,
    ) -> Template:
        """Create a new template."""
        template_id = str(uuid.uuid4())[:8]
        now = time.time()

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO templates
                   (template_id, name, description, genre, rhythm, reference,
                    tone, duration_target, additional_instructions, provider,
                    resolution, fps, crf, created_at, updated_at, use_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    template_id, name, description, genre, rhythm, reference,
                    tone, duration_target, additional_instructions, provider,
                    resolution, fps, crf, now, now,
                ),
            )
            conn.commit()

        logger.info(f"Template created: '{name}' ({template_id})")
        return self.get(template_id)

    def get(self, template_id: str) -> Template | None:
        """Get a template by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM templates WHERE template_id = ?",
                (template_id,),
            ).fetchone()

        if not row:
            return None
        return self._row_to_template(row)

    def get_by_name(self, name: str) -> Template | None:
        """Get a template by name (case-insensitive)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM templates WHERE LOWER(name) = LOWER(?)",
                (name,),
            ).fetchone()

        if not row:
            return None
        return self._row_to_template(row)

    def list_all(self, limit: int = 50) -> list[Template]:
        """List all templates ordered by use count (most used first)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM templates ORDER BY use_count DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        return [self._row_to_template(row) for row in rows]

    def update(self, template_id: str, **kwargs) -> Template | None:
        """Update template fields. Only updates fields provided in kwargs."""
        allowed = {
            "name", "description", "genre", "rhythm", "reference",
            "tone", "duration_target", "additional_instructions",
            "provider", "resolution", "fps", "crf",
        }

        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return self.get(template_id)

        updates["updated_at"] = time.time()

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [template_id]

        with self._conn() as conn:
            conn.execute(
                f"UPDATE templates SET {set_clause} WHERE template_id = ?",
                values,
            )
            conn.commit()

        return self.get(template_id)

    def delete(self, template_id: str) -> bool:
        """Delete a template. Returns True if deleted."""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM templates WHERE template_id = ?",
                (template_id,),
            )
            conn.commit()
        return cursor.rowcount > 0

    def increment_use_count(self, template_id: str):
        """Increment the use counter for a template."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE templates SET use_count = use_count + 1, updated_at = ? WHERE template_id = ?",
                (time.time(), template_id),
            )
            conn.commit()

    def _row_to_template(self, row: sqlite3.Row) -> Template:
        """Convert SQLite row to Template dataclass."""
        d = dict(row)
        return Template(**d)


# ─── Built-in Presets ───────────────────────────────────────────────────────────

BUILTIN_PRESETS = [
    {
        "name": "Thriller - Fincher",
        "description": "Dark, cold, precise. Inspired by Se7en and Gone Girl.",
        "genre": "thriller",
        "rhythm": "variable",
        "reference": "David Fincher - Se7en, Gone Girl",
        "tone": "tension creciente, inquietud latente",
        "additional_instructions": "Usar silencios como herramienta de tension. Cortes secos en momentos de revelacion. Color frio desaturado.",
    },
    {
        "name": "Documentary - Neutral",
        "description": "Clean, informative, observational. Natural pacing.",
        "genre": "documental",
        "rhythm": "medio",
        "reference": "Ken Burns, Planet Earth",
        "tone": "objetivo, contemplativo",
        "additional_instructions": "Priorizar L-cuts para continuidad de audio. Cross dissolves entre secciones tematicas. Color natural sin estilizar.",
    },
    {
        "name": "Action - Fast Cuts",
        "description": "High energy, rapid cuts synced to beats. Maximum impact.",
        "genre": "accion",
        "rhythm": "muy_rapido",
        "reference": "Mad Max Fury Road, John Wick",
        "tone": "adrenalina, impacto visceral",
        "additional_instructions": "Cortes cada 1-2 segundos. Sincronizar con beats. Hard cuts exclusivamente. Alto contraste. Momentos de slow motion solo en el climax.",
    },
    {
        "name": "Romance - Dreamy",
        "description": "Soft, warm, slow. Emphasizes emotion and connection.",
        "genre": "romance",
        "rhythm": "lento",
        "reference": "Wong Kar-wai - In the Mood for Love",
        "tone": "nostalgico, intimo, melancolico",
        "additional_instructions": "Slow motion generoso. Cross dissolves largos. Temperatura calida. Priorizar primeros planos.",
    },
    {
        "name": "Music Video - Beat Sync",
        "description": "Every cut on a beat. Visual rhythm matches audio perfectly.",
        "genre": "experimental",
        "rhythm": "rapido",
        "reference": "Edgar Wright montage style",
        "tone": "energetico, preciso, ritmico",
        "additional_instructions": "CADA corte debe caer en un beat detectado. Variar escalas de plano rapidamente. Match cuts cuando sea posible. No usar dissolves.",
    },
    {
        "name": "Horror - Slow Burn",
        "description": "Unsettling, slow, with sudden bursts. Dread over shock.",
        "genre": "horror",
        "rhythm": "variable",
        "reference": "Ari Aster - Hereditary, Midsommar",
        "tone": "inquietud lenta, pavor creciente",
        "additional_instructions": "Planos largos que incomodan. Dip to black antes de revelaciones. Silencios prolongados seguidos de cortes abruptos. Desaturar progresivamente.",
    },
    {
        "name": "Cinematic - Villeneuve",
        "description": "Grand scale, deliberate pacing, immersive sound design.",
        "genre": "ciencia_ficcion",
        "rhythm": "lento",
        "reference": "Denis Villeneuve - Blade Runner 2049, Arrival, Dune",
        "tone": "epico, contemplativo, imponente",
        "additional_instructions": "Planos generales dominantes. Ritmo pausado pero con proposito. Cada corte revela escala. Temperatura fria con acentos calidos puntuales.",
    },
]


# ─── Singleton ──────────────────────────────────────────────────────────────────

_template_store: TemplateStore | None = None


def get_template_store() -> TemplateStore:
    """Get or create the singleton TemplateStore instance."""
    global _template_store
    if _template_store is None:
        _template_store = TemplateStore()
        # Seed built-in presets if database is empty
        if not _template_store.list_all(limit=1):
            _seed_builtin_presets(_template_store)
    return _template_store


def _seed_builtin_presets(store: TemplateStore):
    """Insert built-in presets into a fresh database."""
    logger.info("Seeding built-in template presets...")
    for preset in BUILTIN_PRESETS:
        store.create(**preset)
    logger.info(f"  {len(BUILTIN_PRESETS)} presets created")
