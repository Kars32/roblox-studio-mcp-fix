"""
Roblox Studio MCP Compatibility Bridge (v1.1.0)
Fixes stdio deadlocks, handles discovery probes, and auto-binds active Studio sessions.
"""

import sys
import subprocess
import json
import os
import time

# Ensure UTF-8 I/O on Windows
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Locate StudioMCP.exe dynamically
studio_mcp_path = None
versions_dir = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions")
if os.path.exists(versions_dir):
    for root, _, files in os.walk(versions_dir):
        if "StudioMCP.exe" in files:
            studio_mcp_path = os.path.join(root, "StudioMCP.exe")
            break

if not studio_mcp_path:
    sys.stderr.write("Error: Could not locate StudioMCP.exe in %LOCALAPPDATA%\\Roblox\\Versions\n")
    sys.exit(1)

# Pass stderr to DEVNULL to avoid Windows OS pipe buffer deadlock
proc = subprocess.Popen(
    [studio_mcp_path],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    encoding="utf-8",
    bufsize=1
)

def send_to_proc(msg):
    payload = json.dumps(msg) + "\n"
    proc.stdin.write(payload)
    proc.stdin.flush()

def read_from_proc():
    while True:
        line = proc.stdout.readline()
        if not line:
            return None
        line_str = line.strip()
        if not line_str:
            continue
        try:
            return json.loads(line_str)
        except Exception:
            pass

# Perform legacy initialization handshake with StudioMCP.exe
send_to_proc({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {"listChanged": True}},
        "clientInfo": {"name": "antigravity_bridge", "version": "1.1.0"}
    }
})
init_res = read_from_proc()
send_to_proc({"jsonrpc": "2.0", "method": "notifications/initialized"})

def call_proc_tool(name, args, call_id):
    for attempt in range(5):
        send_to_proc({
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": args}
        })
        res = read_from_proc()
        if res and "result" in res:
            content = res["result"].get("content", [])
            is_error = res["result"].get("isError", False)
            if is_error and len(content) > 0 and "Not connected" in content[0].get("text", ""):
                time.sleep(0.3)
                continue
            return res
        time.sleep(0.2)
    return None

active_studio_set = False

def ensure_active_studio():
    global active_studio_set
    if active_studio_set:
        return True
    try:
        res = call_proc_tool("list_roblox_studios", {}, 900)
        if res and "result" in res:
            txt = res["result"].get("content", [{}])[0].get("text", "{}")
            data = json.loads(txt)
            studios = data.get("studios", [])
            if studios and studios[0].get("id"):
                sid = studios[0]["id"]
                set_res = call_proc_tool("set_active_studio", {"studio_id": sid}, 901)
                if set_res and not set_res.get("result", {}).get("isError", False):
                    active_studio_set = True
                    return True
    except Exception:
        pass
    return False

# Main JSON-RPC loop communicating with modern MCP host
while True:
    try:
        line = sys.stdin.readline()
        if not line:
            break
        line_str = line.strip()
        if not line_str:
            continue

        req = json.loads(line_str)
        req_id = req.get("id")
        method = req.get("method")

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": init_res.get("result", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "RobloxStudio", "version": "1.1.0"},
                    "instructions": "Studio MCP Bridge"
                })
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

        elif method == "notifications/initialized":
            pass

        elif method == "server/discover":
            send_to_proc({
                "jsonrpc": "2.0",
                "id": "list_discover",
                "method": "tools/list",
                "params": {}
            })
            list_res = read_from_proc()
            tools_list = list_res.get("result", {}).get("tools", []) if list_res else []
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": tools_list,
                    "serverInfo": {"name": "RobloxStudio", "version": "1.1.0"}
                }
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

        elif method == "tools/list":
            send_to_proc(req)
            resp = read_from_proc()
            if resp:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()

        elif method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name")
            # Only auto-bind studio if calling actions other than list/set
            if tool_name not in ["list_roblox_studios", "set_active_studio"]:
                ensure_active_studio()
            send_to_proc(req)
            resp = read_from_proc()
            if resp:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()

        elif method == "ping":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {}}) + "\n")
            sys.stdout.flush()

        else:
            send_to_proc(req)
            resp = read_from_proc()
            if resp:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()

    except Exception as e:
        pass

proc.terminate()
