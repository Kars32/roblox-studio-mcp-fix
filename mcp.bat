@echo off
setlocal
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python "%~dp0studio_mcp_bridge.py" %*
) else (
    py "%~dp0studio_mcp_bridge.py" %*
)
