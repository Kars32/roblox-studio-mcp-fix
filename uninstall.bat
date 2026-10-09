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
    if exist "%TARGET_DIR%\mcp.bat" (
        del /f /q "%TARGET_DIR%\mcp.bat"
        echo Removed mcp.bat because backup was not found
    ) else (
        echo [NOTICE] Backup file mcp.bat.backup not found.
    )
)

echo.
echo [SUCCESS] Uninstalled Roblox Studio MCP Fix.
echo.
pause
