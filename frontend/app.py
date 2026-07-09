"""
Streamlit Frontend - AI Video Editor

A clean UI for uploading videos, configuring edit parameters,
viewing the AI-generated edit plan, and downloading the rendered output.
"""

import json
import time

import httpx
import streamlit as st

# ─── Configuration ──────────────────────────────────────────────────────────────

API_BASE = "http://localhost:8000"  # Backend API URL
API_KEY = ""  # Set in sidebar or via environment
ALLOWED_EXTENSIONS = ["mp4", "mov", "avi", "mkv", "webm", "m4v", "mts"]

# ─── Page Config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="AI Video Editor",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ─────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1a1a2e;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1.1rem;
        color: #555;
        margin-bottom: 2rem;
    }
    .status-badge {
        padding: 0.3rem 0.8rem;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 600;
        display: inline-block;
    }
    .status-uploaded { background: #e3f2fd; color: #1565c0; }
    .status-analyzing { background: #fff3e0; color: #e65100; }
    .status-analyzed { background: #e8f5e9; color: #2e7d32; }
    .status-rendering { background: #fce4ec; color: #c62828; }
    .status-completed { background: #e8f5e9; color: #1b5e20; }
    .status-failed { background: #ffebee; color: #b71c1c; }
    .timeline-clip {
        background: #f5f5f5;
        border-left: 4px solid #1976d2;
        padding: 1rem;
        margin: 0.5rem 0;
        border-radius: 4px;
    }
    .metric-card {
        background: #fafafa;
        padding: 1rem;
        border-radius: 8px;
        text-align: center;
        border: 1px solid #eee;
    }
</style>
""", unsafe_allow_html=True)

# ─── Session State Init ─────────────────────────────────────────────────────────

if "project_id" not in st.session_state:
    st.session_state.project_id = None
if "edit_plan" not in st.session_state:
    st.session_state.edit_plan = None
if "status" not in st.session_state:
    st.session_state.status = None
if "api_key" not in st.session_state:
    st.session_state.api_key = ""


# ─── Helper Functions ───────────────────────────────────────────────────────────


def api_call(method: str, endpoint: str, **kwargs) -> httpx.Response | None:
    """Make an API call to the backend with error handling and auth."""
    url = f"{API_BASE}{endpoint}"

    # Inject API key header if configured
    headers = kwargs.pop("headers", {})
    if st.session_state.api_key:
        headers["X-API-Key"] = st.session_state.api_key
    kwargs["headers"] = headers

    try:
        with httpx.Client(timeout=600.0) as client:
            response = getattr(client, method)(url, **kwargs)
            if response.status_code == 401:
                st.error("Authentication required. Enter your API key in the sidebar.")
                return None
            if response.status_code == 403:
                st.error("Invalid API key. Check your key in the sidebar.")
                return None
            return response
    except httpx.ConnectError:
        st.error(
            "Could not connect to backend API. "
            "Make sure the server is running: `uvicorn backend.main:app --reload`"
        )
        return None
    except httpx.TimeoutException:
        st.error("Request timed out. The operation may still be processing.")
        return None


def format_timecode(seconds: float) -> str:
    """Format seconds as MM:SS.mmm"""
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"{mins:02d}:{secs:06.3f}"


# ─── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Settings")

    if st.session_state.project_id:
        st.success(f"Project: `{st.session_state.project_id}`")

        if st.session_state.status:
            status = st.session_state.status
            st.markdown(f"Status: **{status}**")

        if st.button("New Project", use_container_width=True):
            st.session_state.project_id = None
            st.session_state.edit_plan = None
            st.session_state.status = None
            st.rerun()
    else:
        st.info("Upload videos to start a project")

    st.markdown("---")
    st.markdown("### Settings")

    api_url = st.text_input("Backend URL", value=API_BASE)
    if api_url != API_BASE:
        API_BASE = api_url

    # Health check
    st.markdown("---")
    if st.button("Check Backend Health"):
        resp = api_call("get", "/health")
        if resp and resp.status_code == 200:
            health = resp.json()
            st.success("Backend is healthy")
            st.json(health)
        else:
            st.error("Backend unavailable")


# ─── Main Content ───────────────────────────────────────────────────────────────

st.markdown('<p class="main-header">AI Video Editor</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="sub-header">Upload clips, let Gemini design the edit, render with FFmpeg</p>',
    unsafe_allow_html=True,
)

# ─── Step 1: Upload ─────────────────────────────────────────────────────────────

st.markdown("## 1. Upload Video Clips")

uploaded_files = st.file_uploader(
    "Drop your video clips here",
    type=ALLOWED_EXTENSIONS,
    accept_multiple_files=True,
    help="Supported formats: MP4, MOV, AVI, MKV, WebM. Max total: 500MB.",
)

if uploaded_files and not st.session_state.project_id:
    col1, col2 = st.columns([3, 1])
    with col1:
        st.info(f"{len(uploaded_files)} file(s) selected, ready to upload")
    with col2:
        if st.button("Upload", type="primary", use_container_width=True):
            with st.spinner("Uploading videos to server..."):
                files = [
                    ("videos", (f.name, f.getvalue(), "video/mp4"))
                    for f in uploaded_files
                ]
                resp = api_call("post", "/api/upload", files=files)

                if resp and resp.status_code == 200:
                    data = resp.json()
                    st.session_state.project_id = data["project_id"]
                    st.session_state.status = data["status"]
                    st.success(data["message"])
                    st.rerun()
                elif resp:
                    st.error(f"Upload failed: {resp.json().get('detail', resp.text)}")

# Show uploaded files if project exists
if st.session_state.project_id:
    resp = api_call("get", f"/api/status/{st.session_state.project_id}")
    if resp and resp.status_code == 200:
        status_data = resp.json()
        st.session_state.status = status_data["status"]

        with st.expander(f"Uploaded Files ({len(status_data['files'])})", expanded=False):
            for f in status_data["files"]:
                st.markdown(f"- `{f}`")

# ─── Step 2: Configure & Analyze ────────────────────────────────────────────────

if st.session_state.project_id:
    st.markdown("---")
    st.markdown("## 2. Configure Edit Parameters")

    # ── Template selector ──
    st.markdown("### Quick Start: Use a Template")

    # Fetch available templates
    templates_list = []
    templates_resp = api_call("get", "/api/templates")
    if templates_resp and templates_resp.status_code == 200:
        templates_list = templates_resp.json().get("templates", [])

    template_names = ["(None - configure manually)"] + [
        f"{t['name']} — {t['description'][:50]}" for t in templates_list
    ]
    selected_template_idx = st.selectbox(
        "Apply Template",
        options=range(len(template_names)),
        format_func=lambda i: template_names[i],
        index=0,
        help="Select a preset to auto-fill all parameters. You can still override individual fields.",
    )

    # Apply template defaults if selected
    tpl_genre = "drama"
    tpl_rhythm = "medio"
    tpl_reference = ""
    tpl_tone = ""
    tpl_duration = ""
    tpl_instructions = ""
    tpl_id = ""

    if selected_template_idx > 0:
        tpl = templates_list[selected_template_idx - 1]
        tpl_id = tpl["template_id"]
        tpl_genre = tpl.get("genre", "drama")
        tpl_rhythm = tpl.get("rhythm", "medio")
        tpl_reference = tpl.get("reference", "")
        tpl_tone = tpl.get("tone", "")
        tpl_duration = tpl.get("duration_target", "")
        tpl_instructions = tpl.get("additional_instructions", "")

    st.markdown("### Manual Configuration")
    st.caption("Override any field below, or leave as-is to use template defaults.")

    col1, col2 = st.columns(2)

    genre_options = [
        "drama", "thriller", "horror", "comedia", "documental",
        "accion", "romance", "ciencia_ficcion", "experimental",
        "musical", "noir", "western",
    ]
    rhythm_options = ["muy_lento", "lento", "medio", "rapido", "muy_rapido", "variable"]

    with col1:
        genre = st.selectbox(
            "Genre",
            options=genre_options,
            index=genre_options.index(tpl_genre) if tpl_genre in genre_options else 0,
            help="The cinematic genre influences pacing, transitions, and color choices",
        )

        rhythm = st.selectbox(
            "Editing Rhythm",
            options=rhythm_options,
            index=rhythm_options.index(tpl_rhythm) if tpl_rhythm in rhythm_options else 2,
            help="Controls cut frequency and clip durations",
        )

        reference = st.text_input(
            "Aesthetic Reference",
            value=tpl_reference,
            placeholder="e.g., Blade Runner 2049, Wes Anderson, Wong Kar-wai",
            help="Director, film, or visual style to emulate",
        )

    with col2:
        tone = st.text_input(
            "Emotional Tone",
            value=tpl_tone,
            placeholder="e.g., melancholic, tense, euphoric, contemplative",
            help="The emotional quality of the final edit",
        )

        duration_target = st.text_input(
            "Target Duration",
            value=tpl_duration,
            placeholder="e.g., 60s, 2min, 90s",
            help="Approximate duration of the final montage",
        )

        additional = st.text_area(
            "Additional Instructions",
            value=tpl_instructions,
            placeholder="e.g., Start with the wide shot, end on the close-up of hands...",
            height=100,
        )

    # ── Save as template ──
    with st.expander("Save current settings as a new template", expanded=False):
        new_tpl_name = st.text_input("Template Name", placeholder="My Thriller Style")
        new_tpl_desc = st.text_input("Description", placeholder="Short description of this style")
        if st.button("Save Template") and new_tpl_name:
            save_payload = {
                "name": new_tpl_name,
                "description": new_tpl_desc,
                "genre": genre,
                "rhythm": rhythm,
                "reference": reference,
                "tone": tone,
                "duration_target": duration_target,
                "additional_instructions": additional,
            }
            save_resp = api_call("post", "/api/templates", json=save_payload)
            if save_resp and save_resp.status_code == 200:
                st.success(f"Template '{new_tpl_name}' saved!")
                st.rerun()
            elif save_resp:
                st.error(save_resp.json().get("detail", "Save failed"))

    # ── Provider selector ──
    st.markdown("### AI Provider")
    provider_choice = st.radio(
        "Select which AI analyzes your videos:",
        options=["auto", "gemini", "nvidia"],
        index=0,
        horizontal=True,
        help=(
            "**auto**: Best available (Gemini first, NVIDIA fallback)\n\n"
            "**gemini**: Google Gemini 2.5 Flash — sees full video + audio natively\n\n"
            "**nvidia**: Qwen 3.5-397B via NVIDIA NIM — analyzes frames, stronger reasoning"
        ),
    )

    # Analyze button
    st.markdown("")
    if st.button("Analyze with AI", type="primary", use_container_width=True):
        with st.spinner("AI is analyzing your videos... This may take 1-3 minutes."):
            payload = {
                "project_id": st.session_state.project_id,
                "genre": genre,
                "rhythm": rhythm,
                "reference": reference,
                "tone": tone,
                "duration_target": duration_target,
                "additional_instructions": additional,
                "provider": provider_choice,
            }
            # Include template_id if one was selected
            if tpl_id:
                payload["template_id"] = tpl_id

            resp = api_call("post", "/api/analyze", json=payload)

            if resp and resp.status_code == 200:
                data = resp.json()
                st.session_state.edit_plan = data["edit_plan"]
                st.session_state.status = data["status"]

                # Show metrics
                mcol1, mcol2, mcol3 = st.columns(3)
                with mcol1:
                    st.metric("Processing Time", f"{data['processing_time']}s")
                with mcol2:
                    st.metric("Tokens In", f"{data['tokens_input']:,}")
                with mcol3:
                    st.metric("Tokens Out", f"{data['tokens_output']:,}")

                st.success(data["message"])
                st.rerun()
            elif resp:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text[:300] if resp.text else f"HTTP {resp.status_code}"
                st.error(f"Analysis failed: {detail}")

# ─── Step 3: View Edit Plan ─────────────────────────────────────────────────────

if st.session_state.edit_plan:
    st.markdown("---")
    st.markdown("## 3. Edit Plan (Timeline)")

    edit_plan = st.session_state.edit_plan
    timeline = edit_plan.get("timeline", edit_plan.get("linea_temporal", []))

    if timeline:
        # Timeline visualization
        for i, clip in enumerate(timeline):
            clip_id = clip.get("id_clip", clip.get("clip_id", f"clip_{i}"))
            tc_in = clip.get("timecode_in", 0)
            tc_out = clip.get("timecode_out", 0)
            justification = clip.get(
                "justificacion_narrativa",
                clip.get("justification", ""),
            )
            transition = clip.get("tipo_corte_posterior", "hard_cut")
            transform = clip.get("transformacion_aplicada", {})

            with st.container():
                st.markdown(
                    f'<div class="timeline-clip">'
                    f'<strong>#{i + 1} - {clip_id}</strong><br>'
                    f'<small>IN: {format_timecode(float(tc_in))} '
                    f'| OUT: {format_timecode(float(tc_out))} '
                    f'| Transition: {transition}</small><br>'
                    f'<em>{justification}</em>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                if transform and transform.get("tipo", "ninguna") != "ninguna":
                    st.caption(
                        f"  Effect: {transform.get('tipo')} "
                        f"(factor: {transform.get('factor', '-')})"
                    )

        st.markdown(f"**Total clips in timeline:** {len(timeline)}")

    # Raw JSON viewer
    with st.expander("View Raw JSON", expanded=False):
        # Allow editing the plan before render
        edited_json = st.text_area(
            "Edit Plan JSON (editable)",
            value=json.dumps(edit_plan, indent=2, ensure_ascii=False),
            height=400,
        )

        if st.button("Update Edit Plan"):
            try:
                st.session_state.edit_plan = json.loads(edited_json)
                st.success("Edit plan updated")
                st.rerun()
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")

    # ── Export to NLE ──
    with st.expander("Export for Premiere / DaVinci / Final Cut", expanded=False):
        st.caption(
            "Download the edit plan in professional formats that can be imported "
            "directly into your NLE for further refinement."
        )

        ecol1, ecol2 = st.columns(2)

        with ecol1:
            st.markdown("**EDL (CMX3600)**")
            st.caption("Universal format. Works with all NLEs. Cuts and transitions only.")
            edl_resp = api_call("get", f"/api/export/{st.session_state.project_id}/edl")
            if edl_resp and edl_resp.status_code == 200:
                st.download_button(
                    label="Download .EDL",
                    data=edl_resp.content,
                    file_name=f"{st.session_state.project_id}_edit.edl",
                    mime="text/plain",
                    use_container_width=True,
                )
            else:
                st.error("EDL export not available")

        with ecol2:
            st.markdown("**FCP XML**")
            st.caption("Rich format with effects, speed changes, audio levels.")
            xml_resp = api_call("get", f"/api/export/{st.session_state.project_id}/xml")
            if xml_resp and xml_resp.status_code == 200:
                st.download_button(
                    label="Download .XML",
                    data=xml_resp.content,
                    file_name=f"{st.session_state.project_id}_edit.xml",
                    mime="application/xml",
                    use_container_width=True,
                )
            else:
                st.error("XML export not available")

# ─── Step 4: Render ─────────────────────────────────────────────────────────────

if st.session_state.edit_plan:
    st.markdown("---")
    st.markdown("## 4. Visual Timeline Preview")
    st.caption("Generate a thumbnail preview to visualize the edit before spending time on a full render.")

    if st.button("Generate Visual Preview", use_container_width=True):
        with st.spinner("Generating timeline preview with thumbnails..."):
            resp = api_call("post", f"/api/preview/{st.session_state.project_id}")

            if resp and resp.status_code == 200:
                preview_data = resp.json()
                st.session_state["preview_data"] = preview_data
                st.success(
                    f"Preview ready: {preview_data['clip_count']} clips, "
                    f"{preview_data['total_duration']:.1f}s total"
                )
                st.rerun()
            elif resp:
                st.error(f"Preview failed: {resp.json().get('detail', resp.text)}")

    # Show preview if available
    if st.session_state.get("preview_data"):
        preview_data = st.session_state["preview_data"]

        # Show clips as a horizontal strip
        cols = st.columns(min(len(preview_data["clips"]), 6))
        for i, clip in enumerate(preview_data["clips"]):
            col_idx = i % len(cols)
            with cols[col_idx]:
                # Try to load thumbnail
                thumb_resp = api_call("get", clip["thumbnail_url"])
                if thumb_resp and thumb_resp.status_code == 200:
                    st.image(thumb_resp.content, caption=f"#{clip['position']} {clip['clip_id']}", use_container_width=True)
                else:
                    st.markdown(f"**#{clip['position']}** {clip['clip_id']}")
                st.caption(f"{clip['timecode_in']:.1f}→{clip['timecode_out']:.1f}s | {clip['transition']}")

        # Link to full HTML timeline
        html_url = preview_data.get("html_url", "")
        if html_url:
            st.markdown(f"[Open full interactive timeline]({API_BASE}{html_url})")

    st.markdown("---")
    st.markdown("## 5. Render Final Video")

    col1, col2, col3 = st.columns(3)
    with col1:
        resolution = st.selectbox(
            "Resolution",
            options=["1920x1080", "3840x2160", "1280x720", "1080x1920"],
            index=0,
        )
    with col2:
        fps = st.selectbox("FPS", options=[24, 25, 30, 48, 60], index=0)
    with col3:
        quality = st.slider("Quality (CRF)", min_value=0, max_value=51, value=18,
                           help="Lower = better quality, larger file. 18 is visually lossless.")

    if st.button("Render Video", type="primary", use_container_width=True):
        with st.spinner("Rendering video with FFmpeg... This may take a while."):
            payload = {
                "project_id": st.session_state.project_id,
                "edit_plan": st.session_state.edit_plan,
                "resolution": resolution,
                "fps": fps,
                "crf": quality,
            }

            resp = api_call("post", "/api/render", json=payload)

            if resp and resp.status_code == 200:
                data = resp.json()
                st.session_state.status = data["status"]

                st.success(data["message"])

                # Metrics
                mcol1, mcol2 = st.columns(2)
                with mcol1:
                    st.metric("Render Time", f"{data['render_time']}s")
                with mcol2:
                    st.metric("Output Size", f"{data['output_size_mb']} MB")

                st.rerun()
            elif resp:
                st.error(f"Render failed: {resp.json().get('detail', resp.text)}")

# ─── Step 6: Download ───────────────────────────────────────────────────────────

if st.session_state.status == "completed" and st.session_state.project_id:
    st.markdown("---")
    st.markdown("## 6. Download Result")

    download_url = f"{API_BASE}/api/download/{st.session_state.project_id}"

    col1, col2 = st.columns([2, 1])
    with col1:
        st.success("Your video is ready!")
        st.markdown(f"[Direct download link]({download_url})")

    with col2:
        # Fetch the file for streamlit download button
        resp = api_call("get", f"/api/download/{st.session_state.project_id}")
        if resp and resp.status_code == 200:
            st.download_button(
                label="Download Video",
                data=resp.content,
                file_name=f"{st.session_state.project_id}_final.mp4",
                mime="video/mp4",
                use_container_width=True,
            )

    # Video preview (if file is small enough)
    st.markdown("### Preview")
    st.info(
        "For large files, download and play locally. "
        "Preview works best with files under 50MB."
    )
    try:
        resp = api_call("get", f"/api/download/{st.session_state.project_id}")
        if resp and resp.status_code == 200 and len(resp.content) < 50 * 1024 * 1024:
            st.video(resp.content)
    except Exception:
        st.caption("Preview not available for this file size")


# ─── Footer ─────────────────────────────────────────────────────────────────────

st.markdown("---")
st.caption(
    "AI Video Editor v1.0 | Powered by Gemini 1.5 Pro + FFmpeg | "
    "Built with FastAPI + Streamlit"
)
