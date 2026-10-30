@echo off
rem Thin alias for "indextts2.cmd api". Keep this file pure ASCII.
call "%~dp0indextts2.cmd" api %*
exit /b %ERRORLEVEL%
