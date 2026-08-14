@echo off
setlocal

echo [1/3] Checking PyInstaller...
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
    if errorlevel 1 (
        echo ERROR: Failed to install PyInstaller. Please ensure pip is available and Python is on your PATH.
        exit /b 1
    )
)

echo [2/3] Building Python backend with PyInstaller...
python -m PyInstaller --noconfirm clockin_server.spec
if errorlevel 1 (
    echo ERROR: PyInstaller build failed. Check the output above for details.
    exit /b 1
)

echo [3/3] Building Electron installer with npm...
npm run build-win
if errorlevel 1 (
    echo ERROR: Electron build failed. Check the output above for details.
    exit /b 1
)

echo.
echo Build complete. The installer can be found in the dist\ folder:
for %%f in (dist\*.exe) do echo   %%f

endlocal
