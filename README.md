# 🛠️ Roblox Studio MCP Compatibility Fix

A lightweight, zero-dependency compatibility bridge for **Roblox Studio Model Context Protocol (MCP)** that fixes protocol discovery crashes and Windows batch script syntax errors in modern MCP clients (**Antigravity**, **Claude Code**, **Cursor**, etc.).

---

## 🛑 The Problems It Solves

### 1. The `server/discover` Protocol Panic
```
Error: expect initialized request, but received: Some(Request(JsonRpcRequest { jsonrpc: JsonRpcVersion2_0, id: Number(1), request: CustomRequest(CustomRequest { method: "server/discover" ... }) }))
: connection closed: calling "initialize": client is closing: EOF
```
* **Why it happens:** Modern MCP hosts send a lightweight `server/discover` probe upon startup to discover available tools. Roblox's Rust-based `StudioMCP.exe` strictly enforces the legacy stateful handshake (`initialize` must be request #1) and crashes when receiving discovery requests.

### 2. The `mcp.bat` Windows Batch Syntax Error
```
'else' is not recognized as an internal or external command, operable program or batch file.
'"%B/..\StudioMCP.exe"' is not recognized as an internal or external command...
'))' is not recognized as an internal or external command...
```
* **Why it happens:** Roblox's default `%LOCALAPPDATA%\Roblox\mcp.bat` has an `if (...)` statement with `else` placed on a new line, which is invalid syntax in Windows `cmd.exe`.

### 3. Automatic Active Studio Detection
* Roblox's `StudioMCP.exe` requires clients to manually call `list_roblox_studios` and `set_active_studio` before executing tools. This bridge automatically resolves and binds your active open Studio place on the fly.

---

## ⚡ Quick Start (1-Click Install)

1. Clone or download this repository.
2. Double-click **`install.bat`**.
3. Restart your MCP client / IDE.

That's it! Your MCP client will now connect to Roblox Studio seamlessly.

---

## 🔧 Manual Installation

If you prefer to install manually:

1. Copy [`studio_mcp_bridge.py`](studio_mcp_bridge.py) into `%LOCALAPPDATA%\Roblox\`.
2. Replace `%LOCALAPPDATA%\Roblox\mcp.bat` with:
   ```bat
   @echo off
   python "%LOCALAPPDATA%\Roblox\studio_mcp_bridge.py" %*
   ```
3. Configure your client's MCP configuration (`mcp_config.json`):
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

## 🏗️ Architecture

```
MCP Host (Antigravity / Claude Code / Cursor)
       │
       │  Sends JSON-RPC over stdio (supports server/discover & initialize)
       ▼
[ studio_mcp_bridge.py ]
       ├── Handles initial handshake with StudioMCP.exe
       ├── Answers host discovery probes with full tool catalog
       ├── Auto-binds the active Roblox Studio instance ID
       ▼
StudioMCP.exe (Roblox Official Binary)
       │  Local WebSocket IPC (port ~44933)
       ▼
Roblox Studio (Active Place / Live Session)
```

---

## 📦 Requirements

* **OS:** Windows 10 / 11
* **Python:** 3.8+ (Included standard with Python on PATH)
* **Roblox Studio:** Studio with Model Context Protocol enabled

---

## 🗑️ Uninstallation

Run **`uninstall.bat`** to restore your original Roblox Studio `mcp.bat` and remove the bridge script.

---

## 📄 License

MIT License © 2026
