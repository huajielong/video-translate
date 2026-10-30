@echo off
rem ---------------------------------------------------------------------------
rem IndexTTS2 CLI launcher (Windows).
rem
rem IMPORTANT: this file must stay pure ASCII. cmd.exe parses the whole batch
rem file using the OEM codepage (936/GBK on Chinese Windows) BEFORE `chcp 65001`
rem takes effect, so UTF-8 comments turn into mojibake that corrupts the
rem following `set` commands. All localized messages are printed by the Python
rem side instead, which handles UTF-8 correctly.
rem
rem Two things that must be right:
rem   1. The project's `venv` is NOT a real virtualenv (no pyvenv.cfg, no
rem      Scripts\python.exe); the interpreter sits directly in venv\. So
rem      venv\python.exe is hardcoded, and venv\ is prepended to PATH -- which
rem      is also how ffmpeg.exe gets found.
rem   2. chcp 65001 + PYTHONUTF8 keep Chinese text and paths from being mangled.
rem ---------------------------------------------------------------------------

setlocal
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "SKILL_DIR=%~dp0.."
for %%I in ("%SKILL_DIR%") do set "SKILL_DIR=%%~fI"

rem Project dir: env var wins, then config.json, then the skill itself.
rem The skill is self-contained (it ships its own venv/ and checkpoints/), so
rem falling back to SKILL_DIR is the correct default -- config.json is only
rem needed to point at a DIFFERENT IndexTTS2 installation.
set "PROJECT_DIR=%INDEXTTS2_HOME%"
if not defined PROJECT_DIR if exist "%SKILL_DIR%\config.json" (
  for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-Content -Raw '%SKILL_DIR%\config.json' | ConvertFrom-Json).project_dir" 2^>nul`) do set "PROJECT_DIR=%%P"
)
if not defined PROJECT_DIR set "PROJECT_DIR=%SKILL_DIR%"

rem Interpreter candidates, same order as conf.resolve_python_exe():
rem   bundled layout (venv\python.exe) -> uv layout (venv\Scripts\python.exe) -> PATH
set "PY="
if exist "%PROJECT_DIR%\venv\python.exe" set "PY=%PROJECT_DIR%\venv\python.exe"
if not defined PY if exist "%PROJECT_DIR%\venv\Scripts\python.exe" set "PY=%PROJECT_DIR%\venv\Scripts\python.exe"
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
)
if not defined PY (
  echo indextts2: No usable Python found. 1>&2
  echo   Tried: venv\python.exe, venv\Scripts\python.exe, PATH. 1>&2
  echo   Install Python 3.10/3.11 and uv, then run "indextts2.cmd setup". 1>&2
  exit /b 3
)

set "PATH=%PROJECT_DIR%\venv\Scripts;%PROJECT_DIR%\venv;%PATH%"

"%PY%" -u "%SKILL_DIR%\scripts\indextts2.py" %*
exit /b %ERRORLEVEL%
