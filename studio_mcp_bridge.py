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


def find_studio_mcp():
    """Locate the most recently modified StudioMCP.exe in Roblox Versions directory."""
    studio_mcp_path = None
    latest_mtime = -1
    versions_dir = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions")
    if os.path.exists(versions_dir):
        for root, _, files in os.walk(versions_dir):
            if "StudioMCP.exe" in files:
                candidate = os.path.join(root, "StudioMCP.exe")
                try:
                    mtime = os.path.getmtime(candidate)
                    if mtime > latest_mtime:
                        latest_mtime = mtime
                        studio_mcp_path = candidate
                except OSError:
                    if not studio_mcp_path:
                        studio_mcp_path = candidate
    return studio_mcp_path


def main():
    studio_mcp_path = find_studio_mcp()
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
        if proc.poll() is not None:
            return
        payload = json.dumps(msg) + "\n"
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()
        except (IOError, ValueError):
            pass

    def read_from_proc():
        while True:
            if proc.poll() is not None:
                return None
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
        for _ in range(5):
            send_to_proc({
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": args}
            })
            res = read_from_proc()
            if not res:
                break
            if "error" in res:
                return res
            if "result" in res:
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
        nonlocal active_studio_set
        if active_studio_set:
            return True
        try:
            res = call_proc_tool("list_roblox_studios", {}, "bridge_list_studios")
            if res and "result" in res:
                txt = res["result"].get("content", [{}])[0].get("text", "{}")
                data = json.loads(txt)
                studios = data.get("studios", [])
                if studios and studios[0].get("id"):
                    sid = studios[0]["id"]
                    set_res = call_proc_tool("set_active_studio", {"studio_id": sid}, "bridge_set_active_studio")
                    if set_res and not set_res.get("result", {}).get("isError", False):
                        active_studio_set = True
                        return True
        except Exception:
            pass
        return False

    try:
        # Main JSON-RPC loop communicating with modern MCP host
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line_str = line.strip()
            if not line_str:
                continue

            try:
                req = json.loads(line_str)
            except Exception as e:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": f"Parse error: {str(e)}"
                    }
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()
                continue

            req_id = req.get("id")
            method = req.get("method")

            if method == "initialize":
                init_result = (init_res.get("result") if (init_res and isinstance(init_res, dict)) else None) or {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "RobloxStudio", "version": "1.1.0"},
                    "instructions": "Studio MCP Bridge"
                }
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": init_result
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
                tools_list = list_res.get("result", {}).get("tools", []) if (list_res and isinstance(list_res, dict)) else []
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
                tool_name = params.get("name") if isinstance(params, dict) else None
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

    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
