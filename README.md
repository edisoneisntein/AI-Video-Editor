# AI Video Editor

Editor de video cinematografico potenciado por IA. Sube clips, la IA disena el montaje, FFmpeg renderiza el resultado.

```
Clips de video → Scene/Beat Detection → IA genera plan → Preview visual → FFmpeg renderiza → .mp4 final
```

## Features

- **Multi-provider IA**: Gemini 2.5 Flash + NVIDIA NIM (auto-fallback)
- **Scene detection**: detecta cortes naturales y picos de movimiento
- **Beat sync**: detecta BPM, sincroniza cortes con beats del audio
- **7 templates profesionales**: Thriller, Documentary, Action, Romance, Music Video, Horror, Cinematic
- **Timeline preview**: thumbnails + HTML interactivo antes de renderizar
- **Export EDL/XML**: importar en Premiere Pro, DaVinci Resolve, Final Cut Pro
- **Desktop launcher**: .exe que arranca todo con doble-click
- **Seguridad**: auth, rate limiting, CORS, SQLite persistente

## Inicio rapido

### Opcion 1: .exe (sin terminal)

```
Doble-click en: dist/AI Video Editor.exe
```

### Opcion 2: start.bat

```
Doble-click en: start.bat
```

### Opcion 3: Manual

```bash
cd ai-video-editor
cp .env.example .env          # Configurar API keys
pip install -r requirements.txt
uvicorn backend.main:app --port 8000        # Terminal 1
streamlit run frontend/app.py --server.port 8501  # Terminal 2
```

## Requisitos

- Python 3.11+
- FFmpeg en PATH (`winget install Gyan.FFmpeg`)
- API Key: Gemini (https://aistudio.google.com/apikey) y/o NVIDIA (https://build.nvidia.com)

## Configuracion minima (.env)

```env
GEMINI_API_KEY=tu_key_aqui
API_SECRET_KEY=un_token_random_fuerte
AI_PROVIDER=auto
```

## Flujo de trabajo

1. **Upload** — sube clips (.mp4, .mov, .mkv, .avi, .webm)
2. **Template** — selecciona preset o configura manual
3. **Analyze** — IA + scene detection + beat detection → plan de montaje
4. **Preview** — timeline visual con thumbnails (sin renderizar)
5. **Export** (opcional) — EDL/XML para Premiere/DaVinci
6. **Render** — FFmpeg ejecuta el plan → video final .mp4
7. **Download** — descarga el resultado

## API Endpoints (23 rutas)

| Area | Endpoints |
|------|-----------|
| Core | upload, analyze, render, download, status, edit-plan, projects |
| Templates | list, get, create, update, delete |
| Export | EDL (CMX3600), FCP XML |
| Preview | generate, serve assets |
| Public | health, root |

Documentacion interactiva: http://localhost:8000/docs

## Stack

| Capa | Tecnologia |
|------|-----------|
| Backend | FastAPI + Python 3.11 |
| IA | Gemini 2.5 Flash / NVIDIA NIM |
| Video | FFmpeg 8.x (subprocess) |
| Frontend | Streamlit |
| DB | SQLite (WAL mode) |
| Auth | API Key header |
| Rate Limit | slowapi |
| Desktop | PyInstaller (.exe) |

## Documentacion completa

Ver [docs/GUIA_USUARIO.md](docs/GUIA_USUARIO.md) para:
- Configuracion detallada
- Tips para mejores resultados
- Tabla de generos y referencias
- Troubleshooting
- Arquitectura interna

## Licencia

MIT
