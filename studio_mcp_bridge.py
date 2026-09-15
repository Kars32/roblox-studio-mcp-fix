"""
Roblox Studio MCP Compatibility Bridge (v1.2.0)
Fixes stdio deadlocks, handles discovery probes, recovers from disconnections,
and auto-binds active Studio sessions.
"""

import sys
import subprocess
import json
import os
import time


def reconfigure_io():
    """Ensure UTF-8 I/O on Windows or any runtime environment."""
    if sys.stdin is not None and hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def find_studio_mcp_path():
    """
    Locates StudioMCP.exe via STUDIO_MCP_PATH env var or by searching Roblox Versions directories.
    Returns the executable with the latest modification time if multiple versions exist.
    """
    env_path = os.environ.get("STUDIO_MCP_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    search_dirs = [
        os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions"),
        os.path.expandvars(r"%ProgramFiles%\Roblox\Versions"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Roblox\Versions"),
    ]

    candidates = []
    for versions_dir in search_dirs:
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
    def __init__(self, studio_mcp_path=None, proc=None):
        self.studio_mcp_path = studio_mcp_path
        self.proc = proc
        self.active_studio_set = False
        self.init_res = None

    def start_process(self):
        if not self.studio_mcp_path:
            self.studio_mcp_path = find_studio_mcp_path()

        if not self.studio_mcp_path:
            sys.stderr.write("Error: Could not locate StudioMCP.exe in Roblox directory\n")
            sys.exit(1)

        self.proc = subprocess.Popen(
            [self.studio_mcp_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1
        )

    def ensure_proc(self):
        """Ensures that the StudioMCP subprocess is running, restarting and re-initializing if needed."""
        if self.proc is None or self.proc.poll() is not None:
            self.active_studio_set = False
            self.start_process()
            self.init_proc()
        return self.proc is not None and self.proc.poll() is None

    def send_to_proc(self, msg, auto_restart=True):
        if auto_restart:
            if not self.ensure_proc():
                return False
        elif not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            return False

        payload = json.dumps(msg) + "\n"
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def read_from_proc(self, expected_id=None):
        if not self.proc or not self.proc.stdout or self.proc.poll() is not None:
            return None
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
                msg = json.loads(line_str)
            except Exception:
                continue

            if not isinstance(msg, dict):
                continue

            # Pass through server-initiated notifications to client stdout
            if "id" not in msg or msg.get("id") is None:
                if "method" in msg and sys.stdout is not None:
                    sys.stdout.write(json.dumps(msg) + "\n")
                    sys.stdout.flush()
                continue

            if expected_id is not None:
                if str(msg.get("id")) == str(expected_id):
                    return msg
                continue

            return msg

    def init_proc(self):
        req_id = "__bridge_init__"
        if not self.send_to_proc({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "clientInfo": {"name": "antigravity_bridge", "version": "1.2.0"}
            }
        }, auto_restart=False):
            return None
        self.init_res = self.read_from_proc(expected_id=req_id)
        self.send_to_proc({"jsonrpc": "2.0", "method": "notifications/initialized"}, auto_restart=False)
        return self.init_res

    def call_proc_tool(self, name, args, call_id):
        for attempt in range(5):
            req_id = f"__bridge_tool_{call_id}_{attempt}"
            if not self.send_to_proc({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": args}
            }):
                return None
            res = self.read_from_proc(expected_id=req_id)
            if res and isinstance(res, dict) and res.get("id") == req_id:
                if "result" in res and isinstance(res["result"], dict):
                    content = res["result"].get("content", [])
                    is_error = res["result"].get("isError", False)
                    if is_error and isinstance(content, list) and len(content) > 0:
                        first_item = content[0]
                        err_text = first_item.get("text", "") if isinstance(first_item, dict) else ""
                        if "Not connected" in err_text or "No active studio" in err_text:
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
            if res and isinstance(res, dict) and "result" in res and isinstance(res["result"], dict):
                content = res["result"].get("content", [])
                if content and isinstance(content, list) and len(content) > 0:
                    first_item = content[0]
                    txt = first_item.get("text", "{}") if isinstance(first_item, dict) else "{}"
                    data = json.loads(txt)
                    if isinstance(data, dict):
                        studios = data.get("studios", [])
                        if studios and isinstance(studios, list) and len(studios) > 0:
                            first_studio = studios[0]
                            sid = first_studio.get("id") if isinstance(first_studio, dict) else None
                            if sid:
                                set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, "set")
                                if set_res and isinstance(set_res, dict):
                                    result_data = set_res.get("result", {})
                                    if isinstance(result_data, dict) and not result_data.get("isError", False):
                                        self.active_studio_set = True
                                        return True
        except Exception:
            pass
        return False

    def handle_message(self, req):
        if not isinstance(req, dict):
            return None

        req_id = req.get("id")
        method = req.get("method")
        has_id = ("id" in req and req_id is not None)

        if not has_id:
            # Notifications expect no response and should not wait on subprocess
            if method != "notifications/initialized":
                self.send_to_proc(req)
            return None

        if method == "initialize":
            init_result = (self.init_res.get("result") if (self.init_res and isinstance(self.init_res, dict)) else None) or {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "RobloxStudio", "version": "1.2.0"},
                "instructions": "Studio MCP Bridge"
            }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": init_result
            }

        elif method == "notifications/initialized":
            return None

        elif method == "server/discover":
            disc_id = f"list_discover_{req_id}"
            if self.send_to_proc({
                "jsonrpc": "2.0",
                "id": disc_id,
                "method": "tools/list",
                "params": {}
            }):
                list_res = self.read_from_proc(expected_id=disc_id)
                tools_list = list_res.get("result", {}).get("tools", []) if (list_res and isinstance(list_res, dict) and isinstance(list_res.get("result"), dict)) else []
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": tools_list,
                        "serverInfo": {"name": "RobloxStudio", "version": "1.2.0"}
                    }
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": "Failed to query StudioMCP process"}
            }

        elif method == "tools/list":
            if not self.send_to_proc(req):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "Failed to query StudioMCP process"}
                }
            resp = self.read_from_proc(expected_id=req_id)
            if resp is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "StudioMCP process did not respond to tools/list"}
                }
            return resp

        elif method == "tools/call":
            params = req.get("params", {}) if isinstance(req.get("params"), dict) else {}
            tool_name = params.get("name") if isinstance(params, dict) else None

            for attempt in range(2):
                # Auto-bind active studio for actions other than list/set
                if tool_name not in ["list_roblox_studios", "set_active_studio"]:
                    self.ensure_active_studio()

                if not self.send_to_proc(req):
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": "Failed to send tool call to StudioMCP process"}
                    }
                resp = self.read_from_proc(expected_id=req_id)
                if resp is None:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": "StudioMCP process did not respond to tools/call"}
                    }

                if isinstance(resp, dict) and "result" in resp and isinstance(resp["result"], dict):
                    content = resp["result"].get("content", [])
                    if isinstance(content, list) and len(content) > 0:
                        first_item = content[0]
                        err_text = first_item.get("text", "") if isinstance(first_item, dict) else ""
                        if "Not connected" in err_text or "No active studio" in err_text:
                            self.active_studio_set = False
                            if attempt == 0:
                                time.sleep(0.3)
                                continue

                return resp
            return resp

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        else:
            if not self.send_to_proc(req):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": f"Failed to send {method} to StudioMCP process"}
                }
            resp = self.read_from_proc(expected_id=req_id)
            if resp is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": f"StudioMCP process did not respond to {method}"}
                }
            return resp

    def run(self):
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
                except Exception as e:
                    err_resp = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": f"Parse error: {str(e)}"
                        }
                    }
                    if sys.stdout is not None:
                        sys.stdout.write(json.dumps(err_resp) + "\n")
                        sys.stdout.flush()
                    continue

                try:
                    resp = self.handle_message(req)
                    if resp is not None and sys.stdout is not None:
                        sys.stdout.write(json.dumps(resp) + "\n")
                        sys.stdout.flush()
                except Exception as ex:
                    req_id = req.get("id") if isinstance(req, dict) else None
                    if req_id is not None and sys.stdout is not None:
                        err_resp = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32603,
                                "message": f"Internal error: {str(ex)}"
                            }
                        }
                        sys.stdout.write(json.dumps(err_resp) + "\n")
                        sys.stdout.flush()

            except (KeyboardInterrupt, SystemExit):
                break
            except Exception:
                pass

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def main():
    reconfigure_io()
    bridge = StudioMCPBridge()
    try:
        bridge.start_process()
        bridge.init_proc()
        bridge.run()
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
