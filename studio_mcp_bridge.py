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
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def find_studio_mcp_path():
    """
    Locates StudioMCP.exe via STUDIO_MCP_PATH env var or by searching Roblox Versions.
    Returns the executable with the latest modification time if multiple versions exist.
    """
    env_path = os.environ.get("STUDIO_MCP_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    localappdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
    versions_dir = os.path.join(localappdata, "Roblox", "Versions")
    if not os.path.exists(versions_dir):
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
    def __init__(self, studio_mcp_path=None, proc=None):
        self.studio_mcp_path = studio_mcp_path
        self.proc = proc
        self.active_studio_set = False
        self.init_res = None

    def start_process(self):
        if not self.studio_mcp_path:
            self.studio_mcp_path = find_studio_mcp_path()

        if not self.studio_mcp_path:
            sys.stderr.write("Error: Could not locate StudioMCP.exe in %LOCALAPPDATA%\\Roblox\\Versions\n")
            sys.exit(1)

        try:
            self.proc = subprocess.Popen(
                [self.studio_mcp_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1
            )
        except OSError as e:
            sys.stderr.write(f"Error: Failed to start StudioMCP process: {e}\n")
            sys.exit(1)

    def send_to_proc(self, msg):
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            return False
        payload = json.dumps(msg) + "\n"
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def read_from_proc(self):
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
                return json.loads(line_str)
            except Exception:
                pass

    def read_response_for_id(self, req_id, timeout=5.0):
        start_time = time.time()
        while time.time() - start_time < timeout:
            msg = self.read_from_proc()
            if msg is None:
                return None
            if isinstance(msg, dict):
                # If msg is a notification from StudioMCP (no 'id' field or 'id' is None)
                if "id" not in msg or msg.get("id") is None:
                    try:
                        sys.stdout.write(json.dumps(msg) + "\n")
                        sys.stdout.flush()
                    except Exception:
                        pass
                    continue
                if msg.get("id") == req_id:
                    return msg
        return None

    def init_proc(self):
        if not self.send_to_proc({
            "jsonrpc": "2.0",
            "id": "__bridge_init__",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "clientInfo": {"name": "antigravity_bridge", "version": "1.2.0"}
            }
        }):
            return None
        self.init_res = self.read_response_for_id("__bridge_init__")
        self.send_to_proc({"jsonrpc": "2.0", "method": "notifications/initialized"})
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
            res = self.read_response_for_id(req_id)
            if res and isinstance(res, dict):
                result = res.get("result")
                if isinstance(result, dict):
                    content = result.get("content", [])
                    is_error = result.get("isError", False)
                    if is_error and isinstance(content, list) and len(content) > 0:
                        err_item = content[0] if isinstance(content[0], dict) else {}
                        err_text = err_item.get("text", "") if isinstance(err_item, dict) else ""
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
            if res and isinstance(res, dict) and "result" in res:
                result = res.get("result")
                if isinstance(result, dict):
                    content = result.get("content", [])
                    if content and isinstance(content, list) and len(content) > 0:
                        item0 = content[0] if isinstance(content[0], dict) else {}
                        txt = item0.get("text", "{}") if isinstance(item0, dict) else "{}"
                        data = json.loads(txt) if isinstance(txt, str) else {}
                        if isinstance(data, dict):
                            studios = data.get("studios", [])
                            if studios and isinstance(studios, list) and len(studios) > 0:
                                studio0 = studios[0] if isinstance(studios[0], dict) else {}
                                sid = studio0.get("id") if isinstance(studio0, dict) else None
                                if sid is not None:
                                    set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, "set")
                                    if set_res and isinstance(set_res, dict):
                                        set_result = set_res.get("result", {})
                                        if isinstance(set_result, dict) and not set_result.get("isError", False):
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
            init_result = None
            if self.init_res and isinstance(self.init_res, dict):
                init_result = self.init_res.get("result")
            if not isinstance(init_result, dict):
                init_result = {
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
            if self.send_to_proc({
                "jsonrpc": "2.0",
                "id": "list_discover",
                "method": "tools/list",
                "params": {}
            }):
                list_res = self.read_response_for_id("list_discover")
                tools_list = []
                if list_res and isinstance(list_res, dict):
                    res_obj = list_res.get("result")
                    if isinstance(res_obj, dict):
                        t_list = res_obj.get("tools")
                        if isinstance(t_list, list):
                            tools_list = t_list
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
            resp = self.read_response_for_id(req_id)
            if resp is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "StudioMCP process did not respond to tools/list"}
                }
            return resp

        elif method == "tools/call":
            params = req.get("params") if isinstance(req.get("params"), dict) else {}
            tool_name = params.get("name") if isinstance(params, dict) else None
            # Auto-bind active studio for actions other than list/set
            if tool_name not in ["list_roblox_studios", "set_active_studio"]:
                self.ensure_active_studio()

            if not self.send_to_proc(req):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "Failed to send tool call to StudioMCP process"}
                }
            resp = self.read_response_for_id(req_id)
            if resp is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "StudioMCP process did not respond to tools/call"}
                }

            if isinstance(resp, dict) and "result" in resp:
                result = resp.get("result")
                if isinstance(result, dict):
                    content = result.get("content", [])
                    if isinstance(content, list) and len(content) > 0:
                        item0 = content[0] if isinstance(content[0], dict) else {}
                        err_text = item0.get("text", "") if isinstance(item0, dict) else ""
                        if "Not connected" in err_text or "No active studio" in err_text:
                            self.active_studio_set = False

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
            resp = self.read_response_for_id(req_id)
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
                    sys.stdout.write(json.dumps(err_resp) + "\n")
                    sys.stdout.flush()
                    continue

                try:
                    resp = self.handle_message(req)
                    if resp is not None:
                        sys.stdout.write(json.dumps(resp) + "\n")
                        sys.stdout.flush()
                except Exception as e:
                    req_id = req.get("id") if isinstance(req, dict) else None
                    if req_id is not None:
                        err_resp = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32603,
                                "message": f"Internal error: {str(e)}"
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
