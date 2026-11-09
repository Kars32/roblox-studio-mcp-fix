@echo off
if exist "%~dp0studio_mcp_bridge.py" (
    python -u "%~dp0studio_mcp_bridge.py" %*
) else (
    python -u "%LOCALAPPDATA%\Roblox\studio_mcp_bridge.py" %*
)
