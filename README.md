# Roblox Studio MCP Compatibility Bridge

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/Python-3.8+-yellow.svg)](https://www.python.org/)
[![Roblox Studio](https://img.shields.io/badge/Roblox%20Studio-MCP%20Enabled-red.svg)](https://create.roblox.com/)

A lightweight Python bridge for Roblox Studio's Model Context Protocol (MCP) server. Fixes startup crashes, pipe deadlocks, handshake errors, and batching issues when connecting modern MCP clients like Antigravity, Claude Code, Cursor, and OpenCode to Roblox Studio on Windows.

---

## The Issues Fixed

When connecting MCP clients to Roblox Studio, common compatibility issues prevent tools from executing:

### 1. `server/discover` Handshake Panic
```text
Error: expect initialized request, but received: Some(Request(JsonRpcRequest { jsonrpc: JsonRpcVersion2_0, id: Number(1), request: CustomRequest(CustomRequest { method: "server/discover" ... }) }))
: connection closed: calling "initialize": client is closing: EOF
```
Modern MCP hosts send a lightweight `server/discover` probe on startup to scan available tools. Roblox's built-in `StudioMCP.exe` strictly enforces the older stateful initialization sequence (`initialize` must be the first request) and immediately aborts the connection upon receiving a discovery probe.

### 2. Windows Batch Syntax Bug in `mcp.bat`
```cmd
'else' is not recognized as an internal or external command, operable program or batch file.
'"%B/..\StudioMCP.exe"' is not recognized as an internal or external command...
'))' is not recognized as an internal or external command...
```
Roblox's generated `%LOCALAPPDATA%\Roblox\mcp.bat` places the `else` keyword on a separate line after `if (...)`, which is invalid syntax in Windows Command Prompt (`cmd.exe`).

### 3. Subprocess Pipe Deadlock & Non-Blocking Read Timeouts
`StudioMCP.exe` outputs connection diagnostics to `stderr`. Without redirecting `stderr` or using non-blocking reader threads with timeouts, Windows fills OS buffers or hangs standard standard input/output streams, causing AI clients to hang indefinitely.

### 4. Active Studio Session Binding & Re-connection Retries
`StudioMCP.exe` requires clients to query `list_roblox_studios` and call `set_active_studio` before tool execution works. This bridge automatically resolves and binds the active Studio session when running commands and retries calls automatically if Studio disconnects or restarts.

### 5. JSON-RPC 2.0 Batch Request Support
Handles batch requests (JSON arrays containing multiple requests) seamlessly, returning structured JSON-RPC batch responses.

---

## Quick Install (Automatic)

1. Clone or download this repository.
2. Run `install.bat`.
   * Backs up your existing `mcp.bat` to `mcp.bat.backup`.
   * Copies `studio_mcp_bridge.py` and the updated `mcp.bat` to `%LOCALAPPDATA%\Roblox\`.
3. Restart your MCP client / IDE.

> [!NOTE]
> Whenever Roblox Studio updates to a new version, Roblox will overwrite `mcp.bat`. Simply re-run `install.bat` after any Roblox Studio client update.

---

## Requirements

* Windows 10 or 11
* Python 3.8+ on PATH (or `py` launcher)
* Roblox Studio with **Beta Features $\rightarrow$ Model Context Protocol** enabled
* An open Place/Project loaded in Roblox Studio

---

## Manual Installation

If you prefer to install manually:

1. Copy `studio_mcp_bridge.py` into `%LOCALAPPDATA%\Roblox\`.
2. Replace `%LOCALAPPDATA%\Roblox\mcp.bat` with:
   ```bat
   @echo off
   python "%LOCALAPPDATA%\Roblox\studio_mcp_bridge.py" %*
   ```
3. Add the server to your client's MCP configuration (`mcp_config.json`):
   ```json
   {
     "mcpServers": {
       "Roblox_Studio": {
         "command": "cmd.exe",
         "args": [
           "/c",
           "cd /d %LOCALAPPDATA%\\Roblox && .\\mcp.bat"
         ]
       }
     }
   }
   ```

---

## How It Works

```text
MCP Host (Antigravity / Claude Code / Cursor / OpenCode)
       │
       │  JSON-RPC over stdio (server/discover, initialize, tools/*, batch requests)
       ▼
[ studio_mcp_bridge.py ]
       ├── Threaded I/O with 10s request timeouts
       ├── Handles initial handshake with StudioMCP.exe
       ├── Responds to host discovery probes with tool catalog
       ├── Auto-binds active Roblox Studio instance ID & auto-retries on disconnects
       ▼
StudioMCP.exe (Roblox Server) ─── Local WebSocket ───► Roblox Studio
```

---

## Uninstallation

Run `uninstall.bat` to delete `studio_mcp_bridge.py` and restore your original `mcp.bat` from backup.

---

## License

MIT
