@echo off
chcp 65001 >nul
title JARVIS2 · 个人 AI 指挥官
cd /d "%~dp0"

REM 首次运行自动建虚拟环境并装依赖
if not exist ".venv\Scripts\python.exe" (
    echo [JARVIS2] 首次运行，创建虚拟环境并安装依赖...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

echo.
for /f "tokens=2 delims==" %%P in ('findstr /b "PORT=" .env 2^>nul') do set JARVIS_PORT=%%P
if "%JARVIS_PORT%"=="" set JARVIS_PORT=8100
echo [JARVIS2] 启动中... 浏览器打开 http://127.0.0.1:%JARVIS_PORT%
echo [JARVIS2] 按 Ctrl+C 停止
echo.
python -m uvicorn backend.main:app --host 127.0.0.1 --port %JARVIS_PORT%
pause
