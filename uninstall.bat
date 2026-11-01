@echo off
setlocal enabledelayedexpansion

echo =========================================================
echo   Roblox Studio MCP Compatibility Fix - Uninstaller
echo =========================================================
echo.

set "TARGET_DIR=%LOCALAPPDATA%\Roblox"

if exist "%TARGET_DIR%\studio_mcp_bridge.py" (
    del /f /q "%TARGET_DIR%\studio_mcp_bridge.py"
    echo Removed studio_mcp_bridge.py
)

if exist "%TARGET_DIR%\mcp.bat.backup" (
    copy /y "%TARGET_DIR%\mcp.bat.backup" "%TARGET_DIR%\mcp.bat" >nul
    del /f /q "%TARGET_DIR%\mcp.bat.backup"
    echo Restored original mcp.bat from backup
) else (
    echo [NOTICE] Backup file mcp.bat.backup not found.
    findstr /i "studio_mcp_bridge.py" "%TARGET_DIR%\mcp.bat" >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        del /f /q "%TARGET_DIR%\mcp.bat"
        echo Removed bridge mcp.bat to avoid broken wrapper.
    )
)

echo.
echo [SUCCESS] Uninstalled Roblox Studio MCP Fix.
echo.
pause
