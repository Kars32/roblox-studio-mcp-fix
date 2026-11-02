@echo off
setlocal
where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    python "%LOCALAPPDATA%\Roblox\studio_mcp_bridge.py" %*
    exit /b
)

where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    py -3 "%LOCALAPPDATA%\Roblox\studio_mcp_bridge.py" %*
    exit /b
)

echo [ERROR] Python 3 was not found in PATH or via Python Launcher (py). >&2
exit /b 1
