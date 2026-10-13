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
    echo [NOTICE] Backup file mcp.bat.backup not found. Restoring standard mcp.bat execution...
    (
        echo @echo off
        echo setlocal
        echo set "B=%%~dp0"
        echo if exist "%%B%%\..\StudioMCP.exe" ^(
        echo     "%%B%%\..\StudioMCP.exe" %%*
        echo ^) else if exist "%%B%%\StudioMCP.exe" ^(
        echo     "%%B%%\StudioMCP.exe" %%*
        echo ^) else ^(
        echo     echo Error: StudioMCP.exe not found.
        echo     exit /b 1
        echo ^)
    ) > "%TARGET_DIR%\mcp.bat"
)

echo.
echo [SUCCESS] Uninstalled Roblox Studio MCP Fix.
echo.
pause
