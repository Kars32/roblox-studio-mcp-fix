@echo off
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python "%LOCALAPPDATA%\Roblox\studio_mcp_bridge.py" %*
) else (
    py -3 "%LOCALAPPDATA%\Roblox\studio_mcp_bridge.py" %*
)
