@echo off
setlocal enabledelayedexpansion

where python >nul 2>nul
if !ERRORLEVEL! equ 0 (
    python "%LOCALAPPDATA%\Roblox\studio_mcp_bridge.py" %*
) else (
    where py >nul 2>nul
    if !ERRORLEVEL! equ 0 (
        py -3 "%LOCALAPPDATA%\Roblox\studio_mcp_bridge.py" %*
    ) else (
        echo [ERROR] Neither 'python' nor 'py' launcher was found on PATH. >&2
        exit /b 1
    )
)
