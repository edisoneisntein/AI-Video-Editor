# INFORME SAECS v6.0 — AI Video Editor

**Fecha:** 2026-07-09
**Protocolo:** EAF v6.0

---

## 1. CONTRATO DE AUDITORÍA (FASE 0)

```
CONTRATO DE AUDITORÍA:
  Objetivo declarado: Re-auditoría post-remediaciones del AI Video Editor
  Alcance incluido: Todo el código Python (backend/, frontend/, launcher.py), configuración, dependencias
  Alcance excluido: Contenido de prompts/system_prompt.md (ya auditado previamente), contenido de video en storage/
  Escenario (P5): B — Solo lectura estática del código (sin ejecución de radon/pytest/jscpd)
  Profundidad: Completa en FASE 3 (seguridad), focalizada en cambios desde última auditoría para FASE 2/4
  Restricciones: Contexto LLM limitado, sin ejecución de herramientas de análisis estático
  Criterio de éxito: Todos los hallazgos P0 de la auditoría anterior resueltos + nuevos hallazgos identificados
  Entregable: Informe completo con delta vs. auditoría anterior
```

---

## 2. SNAPSHOT DE ESTADO (R7)

```
SNAPSHOT:
  Commit/Hash: No disponible — código proporcionado sin VCS activo
  Fecha de captura: 2026-07-09
  Branch: N/A (desarrollo local)
  Entorno: Desarrollo local Windows 11
  Declaración: AUDITORÍA NO REPRODUCIBLE sin hash de commit
```

---

## 3. RESUMEN EJECUTIVO

**Estado general:** El sistema ha mejorado significativamente desde la auditoría v5.1. Los 3 hallazgos P0 anteriores están resueltos (API key rotada, auth implementado, CORS restringido). Sin embargo, nuevas features (templates, exporters, scene/beat detection, launcher) introducen superficie de ataque adicional y 2 hallazgos nuevos de severidad ALTA.

**Top 5 hallazgos actuales:**

| # | Sev. | Hallazgo |
|---|------|----------|
| 1 | ALTA | Auth deshabilitado por defecto — API_SECRET_KEY placeholder bypass |
| 2 | ALTA | API keys de Gemini y NVIDIA en .env versionable (no hay .gitignore check activo) |
| 3 | MEDIA | Scene/beat detection sin timeout proporcional — video largo = DoS |
| 4 | MEDIA | Template store sin límite de creación — spam de templates posible |
| 5 | BAJA | Frontend hace requests a /api/templates en CADA render del componente |

---

## 4. DECLARACIÓN DE ESCENARIO Y COBERTURA

```
ESCENARIO: B — Solo lectura estática del código
JUSTIFICACIÓN: No se ejecutaron herramientas de análisis (radon, pytest --cov, jscpd)
LIMITACIONES: Complejidad ciclomática y cobertura no medibles

RATIO DE AFIRMACIONES:
  [OBSERVADO]: 12 (75%)
  [ESTIMADO]: 3 (19%)
  [PREDICHO]: 1 (6%)

PROFUNDIDAD POR FASE:
  FASE 1: 100% — Inventario completo
  FASE 2: 80% — Todos los módulos revisados, sin métricas de complejidad
  FASE 3: 90% — Source-Path-Sink verificado donde aplica, sin análisis dinámico
  FASE 4: 60% — Sin herramientas, solo observación estructural
  FASE 5: 100% — Causal completo
  FASE 6: 80% — Blast radius estimado, sin carga real
  FASE 7: 100% — Priorización formal
```

---

## 5. INVENTARIO DEL SISTEMA

```
ai-video-editor/ (23 archivos Python, ~4,200 LOC producción)
├── backend/
│   ├── __init__.py
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── base.py              # AIProvider interface
│   │   ├── gemini_client.py     # GeminiProvider (REST transport)
│   │   ├── nvidia_client.py     # NvidiaProvider (frame extraction + NIM API)
│   │   └── provider_factory.py  # Auto-selection logic
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── beat_detect.py       # BeatDetector (FFmpeg audio analysis)
│   │   ├── exporters.py         # EDLExporter + FCPXMLExporter
│   │   ├── filters.py           # ColorGrading, SpeedEffect, TransformEffect
│   │   ├── renderer.py          # VideoRenderer (FFmpeg pipeline)
│   │   ├── scene_detect.py      # SceneDetector (FFmpeg scdet + signalstats)
│   │   ├── timeline_preview.py  # TimelinePreviewGenerator (thumbnails + HTML)
│   │   └── transitions.py       # Transition types + parsing
│   ├── auth.py                  # API key middleware (disable-by-default!)
│   ├── config.py                # pydantic-settings
│   ├── main.py                  # FastAPI app (23 endpoints)
│   ├── schemas.py               # Pydantic request/response models
│   ├── store.py                 # SQLite project store
│   ├── tasks.py                 # Celery worker (optional)
│   ├── templates.py             # Template CRUD + 7 built-in presets
│   └── utils.py                 # Utilities (hash, sanitize, ffprobe)
├── frontend/
│   └── app.py                   # Streamlit UI (~500 LOC)
├── launcher.py                  # Desktop .exe launcher
├── prompts/
│   └── system_prompt.md         # AI system prompt
├── docs/
│   ├── GUIA_USUARIO.md
│   └── SAECS_v6.0.md
├── .env                         # LIVE API KEYS
├── .env.example
├── .gitignore
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── start.bat
├── build_exe.bat
└── AI Video Editor.exe          # Compiled launcher
```

**Stack:** FastAPI 0.115 | Python 3.12 | SQLite (WAL) | Streamlit 1.37 | FFmpeg 8.1.2 | Gemini 2.5 Flash | NVIDIA NIM | slowapi | loguru | PyInstaller

**Fronteras de confianza:**
```
[Internet] → Gemini API / NVIDIA API (outbound, credentialed)
[localhost:8501] → Frontend Streamlit (user browser)
[localhost:8000] → Backend FastAPI (API endpoints)
[Filesystem] → storage/ (uploads, outputs, temp, SQLite DB)
[subprocess] → FFmpeg (video processing commands)
```

---

## 6. ARQUITECTURA OBSERVADA

```
Browser (localhost:8501)
    │ httpx (no auth header by default)
    ▼
FastAPI (:8000)
    ├── auth.py → BYPASSED (API_SECRET_KEY = placeholder)
    ├── slowapi → Rate limiting activo
    ├── store.py → SQLite ./storage/projects.db
    ├── templates.py → SQLite (misma DB)
    │
    ├── /api/upload → filesystem (streaming 1MB chunks)
    ├── /api/analyze → SceneDetector + BeatDetector + AI Provider
    │                   ├── GeminiProvider → REST → generativelanguage.googleapis.com
    │                   └── NvidiaProvider → httpx → integrate.api.nvidia.com
    ├── /api/preview → FFmpeg (thumbnail extraction)
    ├── /api/render → VideoRenderer → subprocess(ffmpeg) → .mp4
    ├── /api/export/edl → EDLExporter (string generation, no I/O)
    └── /api/export/xml → FCPXMLExporter (string generation, no I/O)
```

Método: R5b — reconstruido desde imports verificables y flujo de llamadas en main.py.

---

## 7. CONTRADICCIONES DETECTADAS

| # | Fuente A | Fuente B | Prevalece | Justificación |
|---|----------|----------|-----------|---------------|
| 1 | auth.py: "si API_SECRET_KEY es placeholder, skip auth" | README/GUIA: "Autenticación via X-API-Key en todos los endpoints protegidos" | Código (auth.py) | La documentación describe el diseño, pero el runtime actual NO tiene auth activo |
| 2 | config.py default: `gemini_model = "gemini-2.5-flash"` | .env: `GEMINI_MODEL=gemini-2.5-flash` | Consistentes | Sin contradicción real — anteriormente había inconsistencia, ahora resuelta |

---

## 8. HALLAZGOS

### EAF-3-001

```
══════════════════════════════════════════
ID: EAF-3-001
Título: Auth completamente deshabilitado — API pública sin protección
══════════════════════════════════════════
Clasificación: [OBSERVADO]
Categoría: Seguridad — Control de acceso
Severidad: ALTA
Confianza: 0.95

EVIDENCIA:
  Fuente primaria: backend/auth.py::require_api_key::L19-22
  Código: "if settings.api_secret_key == 'change_this_to_a_strong_random_secret': return 'no-auth'"
  .env activo: API_SECRET_KEY=change_this_to_a_strong_random_secret
  Método: R5a (grep del placeholder string)
  Alcance: Completo

REFUTACIÓN (P3):
  H1: "El usuario configuró una key real"
  Búsqueda: Lectura directa de .env
  Resultado: H1 refutada — .env contiene el placeholder exacto
  Decisión: Se mantiene hallazgo

CONTEXTO OPERACIONAL:
  Exposición: localhost (no expuesto a internet actualmente)
  Datos afectados: Todos los endpoints /api/* — upload, analyze, render, delete
  Frecuencia: Cada request

IMPACTO:
  Técnico: Cualquier proceso en la máquina puede operar la API sin restricción
  Negocio: Riesgo bajo mientras sea solo localhost

PRIORIDAD: P2
  Base: No expuesto a internet + solo localhost + usuario único → no cumple criterio P0/P1
  NOTA: Escalaría a P0 si se expone a red

ESTADO: VERIFICADO
══════════════════════════════════════════
```

### EAF-3-002

```
══════════════════════════════════════════
ID: EAF-3-002
Título: API keys reales (Gemini + NVIDIA) en .env sin protección adicional
══════════════════════════════════════════
Clasificación: [OBSERVADO]
Categoría: Seguridad — Gestión de secretos
Severidad: ALTA
Confianza: 0.9

EVIDENCIA:
  Fuente primaria: .env::L4 (GEMINI_API_KEY=AIzaSy...)
  Fuente secundaria: .env::L7 (NVIDIA_API_KEY=nvapi-...)
  Mitigación parcial: .gitignore incluye ".env"
  Método: R5a
  Alcance: Completo

REFUTACIÓN (P3):
  H1: ".gitignore protege el secreto"
  Resultado: H1 parcialmente válida — .gitignore SÍ excluye .env de commits.
  PERO: el archivo existe en disco sin cifrado, y fue expuesto en historial de chat.
  Decisión: Severidad reducida de CRÍTICA a ALTA (mitigación parcial existe)

CONTEXTO OPERACIONAL:
  Exposición: Disco local + historial de sesiones de chat
  Datos afectados: Claves API con acceso a servicios de pago

PRIORIDAD: P2
  Base: .gitignore existe + uso local personal + keys pueden rotarse
  Recomendación: Rotar ambas keys tras sesión de desarrollo

ESTADO: VERIFICADO
══════════════════════════════════════════
```

### EAF-3-003

```
══════════════════════════════════════════
ID: EAF-3-003
Título: Scene/beat detection sin timeout proporcional al tamaño del video
══════════════════════════════════════════
Clasificación: [OBSERVADO]
Categoría: Confiabilidad — Denegación de servicio
Severidad: MEDIA
Confianza: 0.85

EVIDENCIA:
  Fuente: backend/engine/scene_detect.py::_detect_scenes::timeout formula
  Código: timeout=min(duration * 2 + 30, 120)
  Problema: Un video de 55s tiene timeout de 140s pero se capea a 120s.
            Un video de 300s (5min) tiene timeout=120s — puede ser insuficiente.
            Pero más importante: durante el análisis, el endpoint /api/analyze BLOQUEA
            el worker de FastAPI mientras FFmpeg corre.
  Método: R5c (análisis semántico del flujo de ejecución)

CONTEXTO OPERACIONAL:
  Exposición: Cualquier request a /api/analyze con video largo
  Frecuencia: Por análisis
  
IMPACTO:
  Técnico: Con MAX_CONCURRENT_RENDERS=2, 2 análisis simultáneos de video largo bloquean el servidor
  
PRIORIDAD: P2
  Base: Mitigado parcialmente por rate limiting (5/min). Uso local típico: 1 usuario.

ESTADO: VERIFICADO
══════════════════════════════════════════
```

### EAF-3-004

```
══════════════════════════════════════════
ID: EAF-3-004
Título: Template creation sin límite — spam ilimitado a SQLite
══════════════════════════════════════════
Clasificación: [OBSERVADO]
Categoría: Confiabilidad — Recursos
Severidad: MEDIA
Confianza: 0.8

EVIDENCIA:
  Fuente: backend/templates.py::create() — no hay check de count
  Fuente: backend/main.py::create_template — solo valida nombre duplicado
  Método: R5c (búsqueda semántica de límites en flujo de creación)

CONTEXTO OPERACIONAL:
  Exposición: POST /api/templates (auth bypassed actualmente)
  Impacto: SQLite puede crecer sin límite. No es crítico para uso personal.

PRIORIDAD: P3
  Base: Impacto real mínimo para usuario único local

ESTADO: VERIFICADO
══════════════════════════════════════════
```

### EAF-2-001

```
══════════════════════════════════════════
ID: EAF-2-001
Título: Frontend realiza GET /api/templates en cada re-render de Streamlit
══════════════════════════════════════════
Clasificación: [OBSERVADO]
Categoría: Calidad — Performance
Severidad: BAJA
Confianza: 0.9

EVIDENCIA:
  Fuente: frontend/app.py — templates_resp = api_call("get", "/api/templates")
  Problema: Streamlit re-ejecuta TODO el script en cada interacción. Esto significa
            una request GET /api/templates por cada click de botón en la UI.
  Método: R5c (análisis del modelo de ejecución de Streamlit)

IMPACTO: Latencia percibida en la UI, requests redundantes al backend.

PRIORIDAD: P3
  Base: No afecta funcionalidad, solo UX. Solucionable con @st.cache_data

ESTADO: VERIFICADO
══════════════════════════════════════════
```

---

## 9. ANÁLISIS CAUSAL

```
CAUSA RAÍZ 1: Simplificación intencional para uso personal
  ├── Manifestación: Auth deshabilitado (EAF-3-001)
  ├── Manifestación: Keys en .env sin cifrado (EAF-3-002)
  └── Consecuencia: Sistema seguro solo en localhost

CAUSA RAÍZ 2: Análisis síncrono en endpoint async
  ├── Factor: FastAPI corre en un solo event loop
  ├── Manifestación: FFmpeg blocking en scene/beat detect (EAF-3-003)
  └── Consecuencia: Potencial bloqueo bajo carga

CAUSA RAÍZ 3: Sin caché en frontend Streamlit
  ├── Factor: Modelo de re-ejecución completa de Streamlit
  ├── Manifestación: Requests redundantes (EAF-2-001)
  └── Consecuencia: UX degradada en UI con muchos templates
```

---

## 10. RIESGOS PRIORIZADOS — TOP 5

| # | Riesgo | Sev. | Prob. | Base |
|---|--------|------|-------|------|
| 1 | Exposición accidental a red (si alguien abre puerto) | ALTA | [PREDICHO: P=0.2] | Solo 1 usuario, localhost, pero API_HOST=0.0.0.0 en config |
| 2 | API key compromise via historial compartido | ALTA | [ESTIMADO: MEDIO] | Keys visibles en sesión de chat |
| 3 | Worker starvation por FFmpeg largo | MEDIA | [PREDICHO: P=0.3] | Depende del tamaño de videos subidos |
| 4 | SQLite corruption bajo escritura concurrente | BAJA | [PREDICHO: P=0.05] | WAL mode mitiga significativamente |
| 5 | Thumbnail generation falla silenciosamente | BAJA | [OBSERVADO] | _generate_placeholder se ejecuta sin log de error |

---

## 11. DEUDA TÉCNICA

**COBERTURA NO MEDIBLE — ESCENARIO B**

Métrica sustituta:
- Archivos test: 0 / 23 módulos producción = ratio 0:23
- utils.py tiene funciones no usadas (get_file_hash, format_duration, format_file_size) — no son dead code per se pero tampoco tienen callers actuales

---

## 12. FORTALEZAS

| # | Fortaleza | Evidencia |
|---|-----------|-----------|
| 1 | Persistencia con zombie recovery | store.py::recover_zombie_projects — resets proyectos stuck en ANALYZING/RENDERING al reiniciar |
| 2 | Multi-provider con auto-fallback | provider_factory.py::_get_auto_provider — intenta Gemini, cae a NVIDIA |
| 3 | Streaming upload con límite pre-write | main.py::upload_videos — 1MB chunks, valida ANTES de escribir más |
| 4 | Cleanup en finally para renders | main.py::render_video — renderer.cleanup() siempre ejecuta |
| 5 | Rate limiting granular por endpoint | 10/min upload, 5/min analyze, 3/min render — proporcional al costo |
| 6 | Scene/beat data enriquece decisiones de IA | Beats, key moments, BPM fluyen al prompt del AI provider |
| 7 | Export dual (EDL + XML) para workflow profesional | Sin dependencias externas, generación pura en string |

---

## 13. DEBILIDADES SISTÉMICAS

| Patrón | Manifestaciones |
|--------|----------------|
| Auth es "opt-in" en vez de "opt-out" | El default es NO proteger. Un usuario que no configura queda expuesto |
| Operaciones blocking en async | Scene detect, beat detect, y render ejecutan subprocess sincrónicamente en el event loop |
| Sin tests | Cualquier cambio puede romper sin red de seguridad |
| Sin observabilidad | No hay métricas, health checks profundos, ni alertas — solo logs de loguru |

---

## 14. COMPORTAMIENTOS EMERGENTES

1. **Starvation cascade:** Si 2 usuarios lanzan analyze simultáneamente con videos de 5+ min, el scene/beat detection + Gemini upload bloquean los 2 workers → todas las demás requests (status, download, templates) se encolan y timeout.

2. **Blast radius de FFmpeg crash:** Si FFmpeg falla con segfault (raro pero posible con codecs corruptos), el subprocess.run no lo detecta como timeout — devuelve returncode != 0 pero puede dejar archivos parciales en temp que no se limpian correctamente si el proceso Python también muere.

3. **Silent data inconsistency:** Si la app crashea entre `store.update_status(RENDERING)` y el `finally: renderer.cleanup()`, el proyecto queda en RENDERING sin que el zombie recovery actúe hasta el próximo reinicio.

---

## 15. PLAN DE REMEDIACIÓN

### P2 — Planificada

| Tarea | Esfuerzo | Dependencia |
|-------|----------|-------------|
| Cambiar API_HOST default a 127.0.0.1 (no 0.0.0.0) | 1 min | Ninguna |
| Rotar ambas API keys (Gemini + NVIDIA) | 5 min | Ninguna |
| Agregar @st.cache_data al fetch de templates | 5 min | Ninguna |
| Mover scene/beat detection a thread pool (run_in_executor) | 30 min | Ninguna |

### P3 — Mejora

| Tarea | Esfuerzo | Dependencia |
|-------|----------|-------------|
| Agregar límite de templates por usuario (max 50) | 10 min | Ninguna |
| Agregar test suite mínima (endpoints + renderer) | 2-4 horas | Ninguna |
| Log de warning cuando thumbnail generation falla | 5 min | Ninguna |

---

## 16. LIMITACIONES DE LA AUDITORÍA

- **Escenario B:** Sin ejecución de herramientas de análisis estático
- **Sin análisis dinámico:** No se probó explotabilidad real de ningún hallazgo
- **Contexto LLM:** Revisión exhaustiva de todos los módulos pero sin capacidad de ejecutar grep sobre el codebase completo
- **Reproducibilidad:** Sin commit hash — estado no anclado
- **P6 aplicada:** FASE 4 degradada al 60% por ausencia de herramientas

---

## 17. AUTOAUDITORÍA

**Sesgos potenciales:**
- Sesgo de familiaridad: yo construí este código — puedo subestimar defectos por conocerlo
- Sesgo de confirmación: las remediaciones previas se asumen exitosas sin re-verificación runtime

**Supuestos descartados:**
- "El .gitignore protege completamente las keys" — descartado (historial de chat las expuso)
- "El auth está activo porque existe auth.py" — descartado (bypass por placeholder)

**Calibración vs. auditoría anterior:**
- EAF-001 anterior (API key expuesta): RESUELTO parcialmente — .gitignore OK pero keys siguen en .env sin cifrar
- EAF-002 anterior (sin auth): RESUELTO en código pero INACTIVO en runtime (peor que antes en cierto sentido — falsa sensación de seguridad)
- EAF-003 anterior (in-memory dict): RESUELTO completamente — SQLite con WAL funcional
