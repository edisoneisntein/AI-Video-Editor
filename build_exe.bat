@echo off
echo ============================================
echo   Building AI Video Editor .exe
echo ============================================
echo.

REM Check if pyinstaller is installed
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

echo.
echo Building launcher .exe ...
echo.

pyinstaller --onefile --noconsole --name "AI Video Editor" launcher.py

echo.
echo ============================================
if exist "dist\AI Video Editor.exe" (
    echo   BUILD SUCCESSFUL!
    echo   Output: dist\AI Video Editor.exe
    echo.
    echo   To distribute, copy this folder structure:
    echo     AI Video Editor.exe
    echo     .env               (user must configure API keys)
    echo     backend\           (entire folder)
    echo     frontend\          (entire folder)
    echo     prompts\           (entire folder)
    echo     storage\           (empty folders)
    echo     requirements.txt   (for reference)
    echo.
    echo   The user also needs:
    echo     - Python 3.11+ installed
    echo     - pip install -r requirements.txt (done once)
    echo     - FFmpeg in PATH (for rendering)
) else (
    echo   BUILD FAILED. Check errors above.
)
echo ============================================
pause
