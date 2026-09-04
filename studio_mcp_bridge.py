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

def find_studio_mcp_path(versions_dir=None):
    if versions_dir is None:
        versions_dir = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions")

    if not os.path.exists(versions_dir):
        return None

    candidates = []
    for root, _, files in os.walk(versions_dir):
        if "StudioMCP.exe" in files:
            path = os.path.join(root, "StudioMCP.exe")
            try:
                mtime = os.path.getmtime(path)
                candidates.append((mtime, path))
            except OSError:
                pass

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]

studio_mcp_path = find_studio_mcp_path()
if __name__ == "__main__" and not studio_mcp_path:
    sys.stderr.write("Error: Could not locate StudioMCP.exe in %LOCALAPPDATA%\\Roblox\\Versions\n")
    sys.exit(1)

proc = None
if __name__ == "__main__":
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
    if proc and proc.stdin:
        payload = json.dumps(msg) + "\n"
        proc.stdin.write(payload)
        proc.stdin.flush()

def read_from_proc():
    if not proc or not proc.stdout:
        return None
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

init_res = None
if __name__ == "__main__":
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
        if res:
            if "result" in res:
                content = res["result"].get("content", [])
                is_error = res["result"].get("isError", False)
                if is_error and len(content) > 0 and "Not connected" in content[0].get("text", ""):
                    time.sleep(0.3)
                    continue
                return res
            elif "error" in res:
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
            content = res["result"].get("content", [])
            if content and isinstance(content, list):
                txt = content[0].get("text", "{}")
                data = json.loads(txt)
                studios = data.get("studios", [])
                if studios and isinstance(studios, list) and len(studios) > 0:
                    sid = studios[0].get("id")
                    if sid:
                        set_res = call_proc_tool("set_active_studio", {"studio_id": sid}, 901)
                        if set_res and not set_res.get("result", {}).get("isError", False):
                            active_studio_set = True
                            return True
    except Exception:
        pass
    return False

def check_and_handle_disconnection(resp):
    global active_studio_set
    if not resp or "result" not in resp:
        return
    content = resp["result"].get("content", [])
    if isinstance(content, list) and len(content) > 0:
        err_text = content[0].get("text", "")
        if "Not connected" in err_text or "No active studio" in err_text:
            active_studio_set = False

def run_bridge_loop():
    global active_studio_set
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line_str = line.strip()
            if not line_str:
                continue

            try:
                req = json.loads(line_str)
            except Exception as json_err:
                resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": f"Parse error: {str(json_err)}"
                    }
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
                continue

            req_id = req.get("id")
            method = req.get("method")
            is_notification = "id" not in req

            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": (init_res.get("result") if (init_res and isinstance(init_res, dict) and "result" in init_res) else {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": True}},
                        "serverInfo": {"name": "RobloxStudio", "version": "1.1.0"},
                        "instructions": "Studio MCP Bridge"
                    })
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()

            elif method == "notifications/initialized":
                send_to_proc(req)

            elif method == "server/discover":
                send_to_proc({
                    "jsonrpc": "2.0",
                    "id": "list_discover",
                    "method": "tools/list",
                    "params": {}
                })
                list_res = read_from_proc()
                tools_list = list_res.get("result", {}).get("tools", []) if (list_res and isinstance(list_res, dict) and "result" in list_res) else []
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
                elif req_id is not None:
                    err_resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": "Internal error: No response from StudioMCP"}
                    }
                    sys.stdout.write(json.dumps(err_resp) + "\n")
                    sys.stdout.flush()

            elif method == "tools/call":
                params = req.get("params", {}) if isinstance(req.get("params"), dict) else {}
                tool_name = params.get("name")
                # Only auto-bind studio if calling actions other than list/set
                if tool_name not in ["list_roblox_studios", "set_active_studio"]:
                    ensure_active_studio()
                send_to_proc(req)
                resp = read_from_proc()
                if resp:
                    check_and_handle_disconnection(resp)
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
                elif req_id is not None:
                    err_resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": "Internal error: No response from StudioMCP"}
                    }
                    sys.stdout.write(json.dumps(err_resp) + "\n")
                    sys.stdout.flush()

            elif method == "ping":
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {}}) + "\n")
                sys.stdout.flush()

            else:
                send_to_proc(req)
                if is_notification:
                    continue
                resp = read_from_proc()
                if resp:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
                elif req_id is not None:
                    err_resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": "Internal error: No response from StudioMCP"}
                    }
                    sys.stdout.write(json.dumps(err_resp) + "\n")
                    sys.stdout.flush()

        except Exception as e:
            pass

    if proc:
        proc.terminate()

if __name__ == "__main__":
    run_bridge_loop()
