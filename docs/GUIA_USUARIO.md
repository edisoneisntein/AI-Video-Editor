# AI Video Editor — Guia de Usuario Completa

**Version:** 1.1.0
**Ultima actualizacion:** Julio 2026

---

## Que es y que hace

AI Video Editor es un **editor de video autonomo impulsado por IA**. Subes clips crudos, describes que estilo quieres, y la IA disena el montaje completo: decide que partes de cada clip usar, en que orden, con que transiciones, color grading, y ritmo. Luego FFmpeg ejecuta ese plan y entrega un `.mp4` renderizado.

**Productos de la app:**

1. **Plan de montaje (JSON)** — instrucciones detalladas de edicion generadas por IA
2. **Video final (.mp4)** — montaje renderizado automaticamente por FFmpeg
3. **Archivos EDL/XML** — para importar en Premiere Pro, DaVinci Resolve, Final Cut Pro
4. **Timeline visual (HTML)** — preview con thumbnails antes de renderizar

---

## Inicio rapido

### Opcion A: Doble-click (sin terminal)

1. Doble-click en `AI Video Editor.exe` (en la carpeta `dist/`)
2. Se abre el navegador automaticamente
3. Ingresa tu API Key en el sidebar
4. Sube clips y edita

### Opcion B: start.bat

Doble-click en `start.bat` — arranca todo y abre el navegador.

### Opcion C: Terminal manual

```bash
cd ai-video-editor
uvicorn backend.main:app --host 127.0.0.1 --port 8000
# En otra terminal:
streamlit run frontend/app.py --server.port 8501
```

---

## Requisitos del sistema

| Requisito | Detalle |
|-----------|---------|
| Python | 3.11 o superior |
| FFmpeg | Instalado y en PATH (necesario para render, preview, scene/beat detection) |
| API Key | Al menos una: Gemini (Google) o NVIDIA NIM |
| RAM | 4GB minimo, 8GB recomendado para renders |
| Disco | Espacio para clips + outputs (los videos no se comprimen durante el proceso) |

---

## Configuracion (.env)

Copia `.env.example` a `.env` y edita:

```env
# OBLIGATORIO — al menos uno:
GEMINI_API_KEY=tu_key_de_gemini
NVIDIA_API_KEY=tu_key_de_nvidia

# OBLIGATORIO — seguridad:
API_SECRET_KEY=un_token_random_fuerte

# Selector de provider:
AI_PROVIDER=auto    # auto | gemini | nvidia

# Opcionales (tienen defaults sensatos):
GEMINI_MODEL=gemini-2.5-flash
NVIDIA_MODEL=nvidia/nemotron-3-nano-omni-30b-a3b-reasoning
CORS_ORIGINS=http://localhost:8501
MAX_UPLOAD_SIZE_MB=500
MAX_CONCURRENT_RENDERS=2
RATE_LIMIT_PER_MINUTE=30
```

Para generar un API secret fuerte:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Flujo de trabajo completo

```
1. UPLOAD         Subes clips de video (.mp4, .mov, .mkv, etc.)
      |
2. TEMPLATE       Seleccionas un preset o configuras manualmente
      |
3. ANALYZE        La IA analiza video + audio + escenas y genera el plan
      |                |
      |          Scene Detection: detecta cortes naturales, motion
      |          Beat Detection: detecta ritmo, BPM, silencios
      |          AI Provider: recibe todo + genera plan de montaje
      |
4. PREVIEW        Ves un timeline visual con thumbnails (sin renderizar)
      |
5. EXPORT (opc)   Descargas EDL/XML para refinar en Premiere/DaVinci
      |
6. RENDER         FFmpeg ejecuta el plan: corta, transforma, ensambla
      |
7. DOWNLOAD       Descargas el .mp4 final
```

---

## Proveedores de IA

| Provider | Como analiza | Ventaja | Cuando usar |
|----------|-------------|---------|-------------|
| **Gemini** | Upload nativo del video completo | Entiende movimiento, audio, temporalidad | Analisis profundo (default) |
| **NVIDIA NIM** | 8 frames extraidos + API OpenAI-compatible | Rapido, modelos open-source, sin lock-in | Fallback o analisis rapido |
| **auto** | Prueba Gemini primero, fallback a NVIDIA | Resiliencia | Recomendado |

Modelos NVIDIA disponibles:
- `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` (video+audio+texto)
- `qwen/qwen3.5-397b-a17b` (video understanding)
- `minimaxai/minimax-m3` (videos hasta 30 min)
- `google/gemma-4-31b-it` (video como frames)

---

## Templates (Presets)

La app incluye 7 presets profesionales listos para usar:

| Template | Genero | Estilo |
|----------|--------|--------|
| Thriller - Fincher | thriller | Frio, preciso, tension silenciosa |
| Documentary - Neutral | documental | Natural, observacional, L-cuts |
| Action - Fast Cuts | accion | 1-2s por corte, sync con beats |
| Romance - Dreamy | romance | Slow motion, cross dissolves, calido |
| Music Video - Beat Sync | experimental | Cada corte en un beat |
| Horror - Slow Burn | horror | Planos largos + explosiones |
| Cinematic - Villeneuve | ciencia_ficcion | Escala epica, pausado |

**Crear tu propio template:**
1. Configura los parametros como quieras
2. Click "Save Template" en el expander
3. Dale un nombre descriptivo
4. Lo encuentras en el dropdown la proxima vez

**Los templates se ordenan por uso** — los que mas usas aparecen primero.

---

## Scene Detection + Beat Sync

La app analiza automaticamente tus clips ANTES de enviarlos a la IA:

### Scene Detection
- Detecta **cambios de escena** (cortes internos en el clip)
- Mide **intensidad de movimiento** por segmento
- Identifica **key moments** (picos de accion, pausas dramaticas)

### Beat Detection
- Detecta **beats/onsets** en el audio
- Estima **BPM** del clip
- Clasifica: musica vs dialogo
- Encuentra **regiones de silencio**

### Como lo usa la IA
- Alinea cortes con beats detectados (60%+ de coincidencia)
- Usa key_moments como puntos optimos de corte
- Adapta duracion de clips al BPM (rapido = cortes cortos)
- Usa silencios para pausas dramaticas

---

## Timeline Preview

Despues del analisis y ANTES de renderizar, puedes generar un **preview visual**:

- Thumbnail del midpoint de cada segmento
- Barras de duracion proporcionales
- Iconos de transicion entre clips
- Badges de efectos (slow-mo, reverse, etc.)
- Color-coding por temperatura de color
- HTML interactivo que se abre en el navegador

**Para que sirve:** Ver si el plan tiene sentido antes de gastar 1-5 minutos en un render. Si algo no te gusta, editas el JSON y vuelves a previsualizar.

---

## Export para NLEs profesionales

Si prefieres refinar el montaje manualmente:

### EDL (CMX3600)
- **Compatibilidad:** Premiere, DaVinci, Avid, cualquier NLE
- **Incluye:** Timecodes SMPTE, transiciones (Cut/Dissolve/Wipe), velocidad, reels
- **Limitaciones:** No incluye color grading ni audio levels
- **Uso:** File > Import > EDL en tu NLE

### FCP XML (v5)
- **Compatibilidad:** Premiere Pro, DaVinci Resolve, Final Cut Pro
- **Incluye:** Clips con file refs, transiciones, efectos de velocidad, audio levels en dB
- **Mas rico que EDL:** Soporta filtros y configuracion completa
- **Uso:** File > Import > XML / File > Import Timeline

---

## Como sacar el maximo provecho

### Tips de clips

- **Varia los planos**: generales + medios + primeros planos + detalles
- **10-60 segundos por clip**: muy cortos limitan, muy largos diluyen
- **Audio limpio**: la IA lo usa para decidir cortes
- **Minimo 3 clips, ideal 5-10**: variedad para un montaje interesante
- **Mismo FPS y resolucion**: evita problemas de render

### Tips de configuracion

- **Usa referencia estetica**: "David Fincher" produce resultados muy distintos a "Wes Anderson"
- **Se especifico en instrucciones**: "Empezar con plano general, climax en clip 3, terminar en silencio"
- **Describe el arco emocional**: "tension creciente que explota en los ultimos 5 segundos"
- **Menciona tecnicas**: "quiero J-cuts", "solo hard cuts", "slow motion en el climax"

### Tips de iteracion

1. **Primer analisis** con template → revisar plan
2. **Editar JSON** directamente si el plan esta 80% bien
3. **Preview visual** para confirmar estructura
4. **Render** solo cuando estes conforme
5. Si no satisface → ajustar JSON (no re-analizar)

**Principio:** El analisis cuesta tokens. El render es gratis (CPU local). El JSON edit es instantaneo.

---

## Tabla de generos

| Genero | Ritmo | Transiciones | Color | Audio |
|--------|-------|-------------|-------|-------|
| drama | Medio-lento | J-cut, L-cut, dissolve | Calido, bajo contraste | Dialogos, silencios |
| thriller | Variable | Hard cut, match cut | Frio, alto contraste | Tension, stingers |
| horror | Lento + explosiones | Dip to black, hard cut | Desaturado, sombrio | Silencios + impactos |
| comedia | Rapido | Hard cut, jump cut | Saturado, brillante | Ritmo musical |
| documental | Medio | Cross dissolve, L-cut | Natural, neutro | Voz over, ambiente |
| accion | Muy rapido | Hard cut, match cut | Alto contraste | Impactos, musica |
| romance | Lento | Cross dissolve | Calido, pastel | Musica suave |
| ciencia_ficcion | Variable | Wipe, zoom, dissolve | Frio azul, neon | Sintetizadores |
| experimental | Impredecible | Atipico | Extremo | Abstracto |
| noir | Medio-lento | Dissolve, dip to black | B/N o desaturado | Jazz, voz grave |

---

## Referencia de la API

Todos los endpoints `/api/*` requieren header `X-API-Key`.

### Core

| Metodo | Endpoint | Descripcion |
|--------|----------|-------------|
| GET | `/` | Info del servicio (publico) |
| GET | `/health` | Health check con providers (publico) |
| POST | `/api/upload` | Subir clips de video |
| POST | `/api/analyze` | Analizar con IA (acepta template_id) |
| POST | `/api/preview/{id}` | Generar timeline visual con thumbnails |
| POST | `/api/render` | Renderizar video final |
| GET | `/api/download/{id}` | Descargar video renderizado |
| GET | `/api/status/{id}` | Estado del proyecto |
| GET | `/api/edit-plan/{id}` | Ver plan de montaje JSON |
| GET | `/api/projects` | Listar todos los proyectos |
| DELETE | `/api/projects/{id}` | Eliminar proyecto |

### Templates

| Metodo | Endpoint | Descripcion |
|--------|----------|-------------|
| GET | `/api/templates` | Listar todos los templates |
| GET | `/api/templates/{id}` | Ver un template |
| POST | `/api/templates` | Crear template nuevo |
| PUT | `/api/templates/{id}` | Actualizar template |
| DELETE | `/api/templates/{id}` | Eliminar template |

### Export

| Metodo | Endpoint | Descripcion |
|--------|----------|-------------|
| GET | `/api/export/{id}/edl` | Descargar EDL (CMX3600) |
| GET | `/api/export/{id}/xml` | Descargar FCP XML |

### Preview Assets

| Metodo | Endpoint | Descripcion |
|--------|----------|-------------|
| GET | `/api/preview/{id}/{file}` | Servir thumbnail o HTML |

---

## Arquitectura del proyecto

```
ai-video-editor/
├── dist/
│   └── AI Video Editor.exe     # Launcher compilado (doble-click)
├── backend/
│   ├── ai/                     # Proveedores de IA
│   │   ├── base.py             # Interfaz abstracta AIProvider
│   │   ├── gemini_client.py    # Google Gemini (upload nativo video)
│   │   ├── nvidia_client.py    # NVIDIA NIM (frames + OpenAI API)
│   │   └── provider_factory.py # Auto-seleccion de provider
│   ├── engine/                 # Motor de procesamiento
│   │   ├── renderer.py         # Pipeline FFmpeg principal
│   │   ├── filters.py          # Color grading, velocidad, transformaciones
│   │   ├── transitions.py      # Tipos de transicion + parsing
│   │   ├── scene_detect.py     # Deteccion de escenas + movimiento
│   │   ├── beat_detect.py      # Deteccion de beats + BPM + silencios
│   │   ├── timeline_preview.py # Generador de preview visual HTML
│   │   └── exporters.py        # EDL + FCP XML export
│   ├── auth.py                 # Autenticacion X-API-Key
│   ├── config.py               # Configuracion (.env)
│   ├── main.py                 # FastAPI app (23 endpoints)
│   ├── schemas.py              # Modelos Pydantic
│   ├── store.py                # SQLite persistencia
│   ├── templates.py            # Templates/presets CRUD
│   ├── tasks.py                # Celery worker (opcional)
│   └── utils.py                # Utilidades
├── frontend/
│   └── app.py                  # Streamlit UI
├── prompts/
│   └── system_prompt.md        # Instrucciones para la IA
├── storage/
│   ├── uploads/                # Clips por proyecto
│   ├── outputs/                # Videos renderizados + previews
│   └── temp/                   # Temporales de render
├── docs/
│   └── GUIA_USUARIO.md         # Este archivo
├── launcher.py                 # Codigo fuente del .exe
├── start.bat                   # Inicio rapido Windows
├── build_exe.bat               # Compilar el .exe
├── .env                        # Variables de entorno (NO commitear)
├── .env.example                # Plantilla
├── requirements.txt            # Dependencias Python
├── Dockerfile                  # Imagen Docker
└── docker-compose.yml          # Stack completo
```

---

## Troubleshooting

| Problema | Solucion |
|----------|----------|
| "ffmpeg no se reconoce" | Instalar FFmpeg: `winget install Gyan.FFmpeg` y reiniciar terminal |
| "SSL CERTIFICATE_VERIFY_FAILED" | Ya corregido internamente (usa REST transport) |
| "401 Missing X-API-Key" | Agregar header o ingresar key en sidebar del frontend |
| "429 Rate limit exceeded" | Esperar 1 minuto |
| "429 Server is busy" | Esperar a que termine el render actual |
| "Template not found" | Verificar template_id existe con GET /api/templates |
| Render produce video negro | Verificar timecodes dentro del rango del clip |
| El .exe no arranca | Verificar que Python esta en PATH y dependencias instaladas |
| Preview sin thumbnails | FFmpeg no instalado o no en PATH |
| "No AI provider available" | Configurar GEMINI_API_KEY o NVIDIA_API_KEY en .env |

---

## Costos estimados

| Operacion | Gemini 2.5 Flash | NVIDIA NIM |
|-----------|------------------|------------|
| Analizar 1 video 30s | ~$0.008 | ~$0.003 |
| Analizar 5 videos 30s | ~$0.04 | ~$0.015 |
| Analizar 1 video 5min | ~$0.08 | ~$0.015 |
| Scene/Beat detection | $0 (local FFmpeg) | $0 (local FFmpeg) |
| Preview con thumbnails | $0 (local FFmpeg) | $0 (local FFmpeg) |
| Renderizar | $0 (local FFmpeg) | $0 (local FFmpeg) |
| Export EDL/XML | $0 (generacion local) | $0 (generacion local) |

---

## Seguridad

- Autenticacion via `X-API-Key` en todos los endpoints protegidos
- CORS restringido a origenes configurados
- Rate limiting por IP (configurable)
- Limite de concurrencia en renders
- Filenames sanitizados contra path traversal
- Upload en streaming (no carga todo en RAM)
- Secretos NUNCA en codigo fuente (solo .env)
- SQLite con WAL mode para persistencia robusta
- Zombie recovery automatico al reiniciar

---

## Actualizaciones y recompilacion

Si modificas el codigo y quieres recompilar el .exe:

```bash
cd ai-video-editor
pyinstaller --onefile --noconsole --name "AI Video Editor" launcher.py
```

El nuevo .exe estara en `dist/AI Video Editor.exe`.
