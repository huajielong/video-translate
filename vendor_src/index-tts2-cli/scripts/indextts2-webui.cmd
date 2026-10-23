@echo off
rem Thin alias for "indextts2.cmd webui". Keep this file pure ASCII.
call "%~dp0indextts2.cmd" webui %*
exit /b %ERRORLEVEL%
