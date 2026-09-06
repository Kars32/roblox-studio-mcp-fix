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
    """
    Locates StudioMCP.exe in the Roblox Versions directory or via STUDIO_MCP_PATH env var.
    Returns the executable with the latest modification time if multiple versions exist.
    """
    env_path = os.environ.get("STUDIO_MCP_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    versions_dir = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions")
    candidates = []
    if os.path.exists(versions_dir):
        for root, _, files in os.walk(versions_dir):
            if "StudioMCP.exe" in files:
                full_path = os.path.join(root, "StudioMCP.exe")
                try:
                    mtime = os.path.getmtime(full_path)
                    candidates.append((mtime, full_path))
                except OSError:
                    candidates.append((0, full_path))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None


class StudioMCPBridge:
    def __init__(self, proc):
        self.proc = proc
        self.active_studio_set = False
        self.init_res = None

    def send_to_proc(self, msg):
        try:
            payload = json.dumps(msg) + "\n"
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def read_from_proc(self):
        while True:
            try:
                line = self.proc.stdout.readline()
            except (OSError, ValueError):
                return None
            if not line:
                return None
            line_str = line.strip()
            if not line_str:
                continue
            try:
                return json.loads(line_str)
            except Exception:
                pass

    def init_proc(self):
        if not self.send_to_proc({
            "jsonrpc": "2.0",
            "id": "__bridge_init__",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "clientInfo": {"name": "antigravity_bridge", "version": "1.1.0"}
            }
        }):
            return None
        self.init_res = self.read_from_proc()
        self.send_to_proc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self.init_res

    def call_proc_tool(self, name, args, call_id):
        for attempt in range(5):
            if not self.send_to_proc({
                "jsonrpc": "2.0",
                "id": f"__bridge_tool_{call_id}_{attempt}",
                "method": "tools/call",
                "params": {"name": name, "arguments": args}
            }):
                return None
            res = self.read_from_proc()
            if res and "result" in res:
                content = res["result"].get("content", [])
                is_error = res["result"].get("isError", False)
                if is_error and len(content) > 0 and "Not connected" in content[0].get("text", ""):
                    self.active_studio_set = False
                    time.sleep(0.3)
                    continue
                return res
            time.sleep(0.2)
        return None

    def ensure_active_studio(self):
        if self.active_studio_set:
            return True
        try:
            res = self.call_proc_tool("list_roblox_studios", {}, "list")
            if res and "result" in res:
                txt = res["result"].get("content", [{}])[0].get("text", "{}")
                data = json.loads(txt)
                studios = data.get("studios", [])
                if studios and studios[0].get("id"):
                    sid = studios[0]["id"]
                    set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, "set")
                    if set_res and not set_res.get("result", {}).get("isError", False):
                        self.active_studio_set = True
                        return True
        except Exception:
            pass
        return False

    def send_response(self, resp):
        try:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        except (OSError, BrokenPipeError):
            pass

    def send_error_response(self, req_id, code, message):
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": code,
                "message": message
            }
        }
        self.send_response(resp)

    def handle_incoming_request(self, req):
        req_id = req.get("id")
        method = req.get("method")
        has_id = ("id" in req and req_id is not None)

        if not has_id:
            # Handle notifications (no response expected)
            if method != "notifications/initialized":
                self.send_to_proc(req)
            return

        # Request handling (response expected)
        if method == "initialize":
            init_result = (self.init_res.get("result") if isinstance(self.init_res, dict) else None) or {
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
            self.send_response(resp)

        elif method == "server/discover":
            if self.send_to_proc({
                "jsonrpc": "2.0",
                "id": "__bridge_discover__",
                "method": "tools/list",
                "params": {}
            }):
                list_res = self.read_from_proc()
                tools_list = list_res.get("result", {}).get("tools", []) if list_res else []
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": tools_list,
                        "serverInfo": {"name": "RobloxStudio", "version": "1.1.0"}
                    }
                }
                self.send_response(resp)
            else:
                self.send_error_response(req_id, -32603, "Failed to send discovery query to StudioMCP process")

        elif method == "tools/list":
            if self.send_to_proc(req):
                resp = self.read_from_proc()
                if resp:
                    self.send_response(resp)
                else:
                    self.send_error_response(req_id, -32603, "StudioMCP server did not respond to tools/list")
            else:
                self.send_error_response(req_id, -32603, "Failed to send request to StudioMCP process")

        elif method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name") if isinstance(params, dict) else None
            if tool_name not in ["list_roblox_studios", "set_active_studio"]:
                self.ensure_active_studio()

            if self.send_to_proc(req):
                resp = self.read_from_proc()
                if resp:
                    if isinstance(resp, dict) and "result" in resp:
                        content = resp["result"].get("content", [])
                        is_error = resp["result"].get("isError", False)
                        if is_error and len(content) > 0 and "Not connected" in content[0].get("text", ""):
                            self.active_studio_set = False
                    self.send_response(resp)
                else:
                    self.send_error_response(req_id, -32603, "StudioMCP server did not respond to tools/call")
            else:
                self.send_error_response(req_id, -32603, "Failed to send request to StudioMCP process")

        elif method == "ping":
            self.send_response({"jsonrpc": "2.0", "id": req_id, "result": {}})

        else:
            if self.send_to_proc(req):
                resp = self.read_from_proc()
                if resp:
                    self.send_response(resp)
                else:
                    self.send_error_response(req_id, -32603, f"StudioMCP server did not respond to {method}")
            else:
                self.send_error_response(req_id, -32603, "Failed to send request to StudioMCP process")


def main():
    studio_mcp_path = find_studio_mcp()

    if not studio_mcp_path:
        sys.stderr.write("Error: Could not locate StudioMCP.exe in %LOCALAPPDATA%\\Roblox\\Versions\n")
        sys.exit(1)

    proc = subprocess.Popen(
        [studio_mcp_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1
    )

    bridge = StudioMCPBridge(proc)
    bridge.init_proc()

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
            except Exception:
                bridge.send_error_response(None, -32700, "Parse error: Invalid JSON")
                continue

            if isinstance(req, dict):
                bridge.handle_incoming_request(req)

        except (KeyboardInterrupt, SystemExit):
            break
        except Exception:
            pass

    try:
        proc.terminate()
    except Exception:
        pass


if __name__ == "__main__":
    main()
