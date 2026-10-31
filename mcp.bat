@echo off
setlocal
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python -u "%LOCALAPPDATA%\Roblox\studio_mcp_bridge.py" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    py -3 -u "%LOCALAPPDATA%\Roblox\studio_mcp_bridge.py" %*
    exit /b %ERRORLEVEL%
)

echo [ERROR] Neither python nor py launcher was found on PATH. >&2
exit /b 1
