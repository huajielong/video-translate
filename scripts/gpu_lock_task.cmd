@echo off
setlocal
set TASK=video-translate-gpu-clock
if /I "%~1"=="remove" (
  schtasks /Delete /TN "%TASK%" /F >nul 2>&1
  "C:\Windows\System32\nvidia-smi.exe" -rgc
  echo Removed %TASK% and reset GPU clocks.
  exit /b 0
)
schtasks /Create /TN "%TASK%" /TR "C:\Windows\System32\nvidia-smi.exe -lgc 300,1200" /SC ONLOGON /RL HIGHEST /F
if errorlevel 1 (
  echo Failed to create %TASK%. Run this file as Administrator.
  exit /b 1
)
schtasks /Run /TN "%TASK%" >nul 2>&1
echo Registered and started %TASK%.
echo To undo: "%~f0" remove
