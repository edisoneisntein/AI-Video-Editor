"""
AI Video Editor — Desktop Launcher

Double-click this (or the compiled .exe) to start the application.
Starts backend API + frontend UI in background, opens browser automatically.

To build .exe:
    pyinstaller --onefile --noconsole --name "AI Video Editor" launcher.py

IMPORTANT: The .exe must be placed in the project ROOT directory (same level
as backend/, frontend/, .env). If built into dist/, copy it up one level.
"""

import os
import subprocess
import sys
import time
import threading
import webbrowser
import socket
from pathlib import Path


# Configuration
BACKEND_PORT = 8000
FRONTEND_PORT = 8501
BROWSER_DELAY = 6  # seconds to wait before opening browser
MAX_WAIT = 20  # max seconds to wait for services to start


def get_app_dir() -> Path:
    """
    Get the application root directory.
    This is where backend/, frontend/, .env etc. live.
    """
    if getattr(sys, "frozen", False):
        # Running as compiled .exe — look for backend/ folder
        exe_dir = Path(sys.executable).parent

        # Check if backend/ is here (exe in project root)
        if (exe_dir / "backend").is_dir():
            return exe_dir

        # Check parent (exe might be in dist/)
        parent = exe_dir.parent
        if (parent / "backend").is_dir():
            return parent

        # Fallback to exe location
        return exe_dir
    else:
        # Running as script
        return Path(__file__).parent


def get_python() -> str:
    """Get the Python executable path."""
    app_dir = get_app_dir()

    # Check for virtual environment
    venv_python = app_dir / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)

    # Check common venv names
    for venv_name in [".venv", "venv", "env"]:
        p = app_dir / venv_name / "Scripts" / "python.exe"
        if p.exists():
            return str(p)

    # Use system python
    return sys.executable if not getattr(sys, "frozen", False) else "python"


def is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port: int, timeout: int = MAX_WAIT) -> bool:
    """Wait until a port is accepting connections."""
    start = time.time()
    while time.time() - start < timeout:
        if is_port_in_use(port):
            return True
        time.sleep(0.5)
    return False


def start_process(cmd: list[str], cwd: str, label: str) -> subprocess.Popen | None:
    """Start a background process without visible window."""
    try:
        kwargs = {
            "cwd": cwd,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }

        if sys.platform == "win32":
            # Hide console window
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            kwargs["startupinfo"] = startupinfo
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        proc = subprocess.Popen(cmd, **kwargs)
        return proc

    except Exception as e:
        show_error(f"Failed to start {label}:\n{e}")
        return None


def start_backend(app_dir: Path, python: str) -> subprocess.Popen | None:
    """Start the FastAPI backend server."""
    if is_port_in_use(BACKEND_PORT):
        return None  # Already running

    cmd = [
        python, "-m", "uvicorn",
        "backend.main:app",
        "--host", "127.0.0.1",
        "--port", str(BACKEND_PORT),
    ]

    return start_process(cmd, str(app_dir), "Backend")


def start_frontend(app_dir: Path, python: str) -> subprocess.Popen | None:
    """Start the Streamlit frontend."""
    if is_port_in_use(FRONTEND_PORT):
        return None  # Already running

    cmd = [
        python, "-m", "streamlit", "run",
        str(app_dir / "frontend" / "app.py"),
        "--server.port", str(FRONTEND_PORT),
        "--server.headless", "true",
        "--server.address", "127.0.0.1",
        "--browser.gatherUsageStats", "false",
        "--browser.serverAddress", "localhost",
    ]

    return start_process(cmd, str(app_dir), "Frontend")


def open_browser_delayed():
    """Open the browser after services are ready."""
    # Wait for frontend to be ready
    if wait_for_port(FRONTEND_PORT, MAX_WAIT):
        time.sleep(1)  # Extra buffer
        webbrowser.open(f"http://localhost:{FRONTEND_PORT}")
    else:
        # Try opening anyway
        time.sleep(BROWSER_DELAY)
        webbrowser.open(f"http://localhost:{FRONTEND_PORT}")


def show_error(message: str):
    """Show error dialog on Windows."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "AI Video Editor - Error", 0x10)
        except Exception:
            print(f"ERROR: {message}")
    else:
        print(f"ERROR: {message}")


def show_running_dialog(backend_proc, frontend_proc):
    """Show 'running' dialog — closing it stops the app."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                (
                    "AI Video Editor esta corriendo.\n\n"
                    f"Frontend: http://localhost:{FRONTEND_PORT}\n"
                    f"Backend:  http://localhost:{BACKEND_PORT}\n"
                    f"API Docs: http://localhost:{BACKEND_PORT}/docs\n\n"
                    "Presiona OK para detener la aplicacion."
                ),
                "AI Video Editor",
                0x40,  # MB_ICONINFORMATION
            )
        except Exception:
            try:
                input("Presiona Enter para detener...")
            except EOFError:
                time.sleep(3600)
    else:
        try:
            input("Presiona Enter para detener...")
        except (KeyboardInterrupt, EOFError):
            pass


def cleanup(*procs):
    """Terminate all background processes."""
    for proc in procs:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def main():
    app_dir = get_app_dir()
    python = get_python()

    # Validate project structure
    if not (app_dir / "backend" / "main.py").exists():
        show_error(
            f"No se encontro el proyecto en:\n{app_dir}\n\n"
            "El .exe debe estar en la misma carpeta que backend/ y frontend/.\n"
            "Si esta en dist/, copialo al directorio raiz del proyecto."
        )
        return

    # Ensure .env exists
    env_file = app_dir / ".env"
    if not env_file.exists():
        example = app_dir / ".env.example"
        if example.exists():
            import shutil
            shutil.copy2(str(example), str(env_file))

    # Refresh PATH to include FFmpeg
    if sys.platform == "win32":
        machine_path = os.environ.get("Path", "")
        user_path = ""
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
            user_path, _ = winreg.QueryValueEx(key, "Path")
            winreg.CloseKey(key)
        except Exception:
            pass
        os.environ["Path"] = machine_path + ";" + user_path

    # Start services
    backend_proc = start_backend(app_dir, python)
    if backend_proc:
        # Wait for backend to be ready before starting frontend
        if not wait_for_port(BACKEND_PORT, 15):
            # Read stderr for error info
            stderr = ""
            try:
                stderr = backend_proc.stderr.read(2000).decode("utf-8", errors="ignore")
            except Exception:
                pass
            show_error(
                f"El backend no inicio correctamente.\n\n"
                f"Python: {python}\n"
                f"Dir: {app_dir}\n\n"
                f"Error:\n{stderr[:500]}"
            )
            cleanup(backend_proc)
            return

    frontend_proc = start_frontend(app_dir, python)

    # Open browser in background thread
    browser_thread = threading.Thread(target=open_browser_delayed, daemon=True)
    browser_thread.start()

    # Show dialog (blocks until user clicks OK)
    show_running_dialog(backend_proc, frontend_proc)

    # Cleanup
    cleanup(backend_proc, frontend_proc)


if __name__ == "__main__":
    main()
