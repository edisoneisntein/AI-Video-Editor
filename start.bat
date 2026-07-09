@echo off
title AI Video Editor
echo ============================================
echo   AI Video Editor — Starting...
echo ============================================
echo.

REM Navigate to script directory
cd /d "%~dp0"

REM Check for virtual environment
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

echo Using Python: %PYTHON%
echo.

REM Start backend in background
echo Starting backend on port 8000...
start /B "" %PYTHON% -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

REM Wait a moment for backend to start
timeout /t 3 /nobreak >nul

REM Start frontend in background
echo Starting frontend on port 8501...
start /B "" %PYTHON% -m streamlit run frontend/app.py --server.port 8501 --server.headless true --browser.gatherUsageStats false

REM Wait for frontend to start
timeout /t 3 /nobreak >nul

REM Open browser
echo Opening browser...
start http://localhost:8501

echo.
echo ============================================
echo   Application is running!
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:8501
echo   API Docs: http://localhost:8000/docs
echo.
echo   Close this window to stop the application.
echo ============================================

REM Keep window open (closing it kills background processes)
pause >nul
