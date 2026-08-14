# Roblox Studio MCP Compatibility Bridge

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/Python-3.8+-yellow.svg)](https://www.python.org/)
[![Roblox Studio](https://img.shields.io/badge/Roblox%20Studio-MCP%20Enabled-red.svg)](https://create.roblox.com/)

A lightweight Python bridge for Roblox Studio's Model Context Protocol (MCP) server. Fixes startup crashes and handshake errors when connecting modern MCP clients like Antigravity, Claude Code, Cursor, and OpenCode to Roblox Studio on Windows.

---

## The Issues

When connecting MCP clients to Roblox Studio, two common errors prevent the connection from establishing:

### 1. `server/discover` Handshake Panic
```
Error: expect initialized request, but received: Some(Request(JsonRpcRequest { jsonrpc: JsonRpcVersion2_0, id: Number(1), request: CustomRequest(CustomRequest { method: "server/discover" ... }) }))
: connection closed: calling "initialize": client is closing: EOF
```
Modern MCP hosts send a lightweight `server/discover` probe on startup to scan available tools. Roblox's built-in `StudioMCP.exe` strictly enforces the older stateful initialization sequence (`initialize` must be the first request) and immediately aborts the connection upon receiving a discovery probe.

### 2. Windows Batch Syntax Bug in `mcp.bat`
```
'else' is not recognized as an internal or external command, operable program or batch file.
'"%B/..\StudioMCP.exe"' is not recognized as an internal or external command...
'))' is not recognized as an internal or external command...
```
Roblox's generated `%LOCALAPPDATA%\Roblox\mcp.bat` places the `else` keyword on a separate line after `if (...)`, which is invalid syntax in Windows Command Prompt (`cmd.exe`).

### 3. Active Studio Session Binding
`StudioMCP.exe` requires clients to query `list_roblox_studios` and call `set_active_studio` before tool execution works. This bridge automatically resolves and binds the active Studio session when running commands.

---

## Quick Install (Automatic)

1. Clone or download this repository.
2. Run `install.bat`.
   * Backs up your existing `mcp.bat` to `mcp.bat.backup`.
   * Copies `studio_mcp_bridge.py` and the updated `mcp.bat` to `%LOCALAPPDATA%\Roblox\`.
3. Restart your MCP client / IDE.

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

```
MCP Host (Antigravity / Claude Code / Cursor / OpenCode)
       │
       │  JSON-RPC over stdio (server/discover, initialize, tools/*)
       ▼
[ studio_mcp_bridge.py ]
       ├── Handles initial handshake with StudioMCP.exe
       ├── Responds to host discovery probes with tool catalog
       ├── Auto-binds active Roblox Studio instance ID
       ▼
StudioMCP.exe (Roblox Server) ─── Local WebSocket ───► Roblox Studio
```

The bridge intercepts incoming requests on `stdin`:
* When the host sends `server/discover`, the bridge queries `tools/list` from `StudioMCP.exe` and formats the discovery response.
* When the host sends `initialize`, the bridge returns the cached capability response.
* When tools like `execute_luau` or `get_studio_state` are called, the bridge ensures the active Studio instance is bound, forwards the request, and pipes the output back to stdout.

---

## Requirements

* Windows 10 or 11
* Python 3.8+ on PATH
* Roblox Studio with Model Context Protocol enabled

---

## Uninstallation

Run `uninstall.bat` to delete `studio_mcp_bridge.py` and restore your original `mcp.bat` from backup.

---

## License

MIT
