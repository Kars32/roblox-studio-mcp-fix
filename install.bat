@echo off
setlocal enabledelayedexpansion

echo =========================================================
echo   Roblox Studio MCP Compatibility Fix - Installer
echo =========================================================
echo.

set "TARGET_DIR=%LOCALAPPDATA%\Roblox"

if not exist "%TARGET_DIR%" (
    echo [NOTICE] Roblox directory not found at %TARGET_DIR%. Creating directory...
    mkdir "%TARGET_DIR%"
)

:: Check for Python
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python was not found in your PATH.
    echo Please install Python 3.8+ and check "Add Python to PATH".
    pause
    exit /b 1
)

echo [1/3] Backing up original mcp.bat...
if exist "%TARGET_DIR%\mcp.bat" (
    if not exist "%TARGET_DIR%\mcp.bat.backup" (
        findstr /i "studio_mcp_bridge.py" "%TARGET_DIR%\mcp.bat" >nul 2>&1
        if !ERRORLEVEL! neq 0 (
            copy /y "%TARGET_DIR%\mcp.bat" "%TARGET_DIR%\mcp.bat.backup" >nul
            echo     Created backup at %TARGET_DIR%\mcp.bat.backup
        ) else (
            echo     Current mcp.bat is already the bridge script; skipping backup.
        )
    ) else (
        echo     Existing backup found at %TARGET_DIR%\mcp.bat.backup
    )
)

echo [2/3] Installing studio_mcp_bridge.py...
copy /y "%~dp0studio_mcp_bridge.py" "%TARGET_DIR%\studio_mcp_bridge.py" >nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to copy studio_mcp_bridge.py to %TARGET_DIR%
    pause
    exit /b 1
)

echo [3/3] Updating mcp.bat...
copy /y "%~dp0mcp.bat" "%TARGET_DIR%\mcp.bat" >nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to update mcp.bat in %TARGET_DIR%
    pause
    exit /b 1
)

echo.
echo =========================================================
echo [SUCCESS] Roblox Studio MCP Fix has been installed!
echo.
echo You can now connect your MCP clients (Antigravity, Claude Code, Cursor, OpenCode, etc.)
echo without any "server/discover" or "mcp.bat" errors.
echo =========================================================
echo.
pause
