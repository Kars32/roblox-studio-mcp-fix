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
    """Ensure UTF-8 line-buffered I/O on Windows or any runtime environment."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", newline="\n")
            except (AttributeError, TypeError, OSError):
                pass


def find_studio_mcp_path():
    """
    Locates StudioMCP.exe via STUDIO_MCP_PATH env var or by searching Roblox Versions.
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
    # Prune non-executable subdirectories to avoid scanning tens of thousands of asset files
    skip_dirs = {"content", "extracontent", "shaders", "textures", "builtinplugins", "builtinstandaloneplugins", "fonts"}

    candidates = []
    for versions_dir in search_dirs:
        if not os.path.exists(versions_dir):
            continue
        for root, dirs, files in os.walk(versions_dir):
            # Prune directory search depth / asset folders
            dirs[:] = [d for d in dirs if d.lower() not in skip_dirs]
            rel_depth = os.path.relpath(root, versions_dir).count(os.sep)
            if rel_depth > 3:
                dirs.clear()

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

        self.proc = subprocess.Popen(
            [self.studio_mcp_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1
        )

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

    def ensure_process_running(self):
        """Checks if process is running; if terminated and executable is available, restarts it."""
        if self.proc is None or self.proc.poll() is not None:
            # If proc was explicitly None (not set or died), only attempt auto-restart
            # if we have a path or can locate one without exiting
            candidate_path = self.studio_mcp_path or find_studio_mcp_path()
            if candidate_path:
                self.active_studio_set = False
                self.start_process()
                self.init_proc()
                return True
            return False
        return True

    def read_from_proc(self, expected_id=None, timeout=None):
        """
        Reads lines from the subprocess until a valid JSON-RPC message is received.
        If expected_id is specified, any incoming notifications or out-of-order messages
        are handled (notifications forwarded directly to stdout) until the matching response is found.
        """
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

            # If it's a notification from StudioMCP (has method, no id), forward to client stdout
            if "method" in msg and ("id" not in msg or msg.get("id") is None):
                try:
                    sys.stdout.write(json.dumps(msg) + "\n")
                    sys.stdout.flush()
                except Exception:
                    pass
                if expected_id is not None:
                    continue
                return msg

            # If expected_id is provided, match it
            if expected_id is not None:
                if msg.get("id") == expected_id:
                    return msg
                # Otherwise, another response was received; continue waiting
                continue

            return msg

    def query_proc(self, msg, expected_id=None):
        """Sends a request to proc and reads the matching response, auto-recovering on failure."""
        if expected_id is None and isinstance(msg, dict):
            expected_id = msg.get("id")

        self.ensure_process_running()

        if not self.send_to_proc(msg):
            # Attempt restart once if send fails
            self.ensure_process_running()
            if not self.send_to_proc(msg):
                return None

        resp = self.read_from_proc(expected_id=expected_id)
        if resp is None and (self.proc is None or self.proc.poll() is not None):
            # Subprocess died during read, try restarting once
            self.ensure_process_running()
            if self.send_to_proc(msg):
                resp = self.read_from_proc(expected_id=expected_id)
        return resp

    def init_proc(self):
        req = {
            "jsonrpc": "2.0",
            "id": "__bridge_init__",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "clientInfo": {"name": "antigravity_bridge", "version": "1.2.0"}
            }
        }
        if not self.send_to_proc(req):
            return None
        self.init_res = self.read_from_proc(expected_id="__bridge_init__")
        self.send_to_proc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self.init_res

    def call_proc_tool(self, name, args, call_id):
        for attempt in range(5):
            req_id = f"__bridge_tool_{call_id}_{attempt}"
            req = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": args}
            }
            res = self.query_proc(req, expected_id=req_id)
            if res and isinstance(res, dict) and res.get("id") == req_id:
                if "result" in res:
                    content = res["result"].get("content", [])
                    is_error = res["result"].get("isError", False)
                    if is_error and isinstance(content, list) and len(content) > 0:
                        err_text = content[0].get("text", "")
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
            if res and "result" in res:
                content = res["result"].get("content", [])
                if content and isinstance(content, list) and len(content) > 0:
                    txt = content[0].get("text", "{}")
                    data = json.loads(txt)
                    studios = data.get("studios", [])
                    if studios and isinstance(studios, list) and len(studios) > 0:
                        sid = studios[0].get("id")
                        if sid is not None and sid != "":
                            set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, "set")
                            if set_res and not set_res.get("result", {}).get("isError", False):
                                self.active_studio_set = True
                                return True
        except Exception:
            pass
        return False

    def handle_single_message(self, req):
        """Processes a single JSON-RPC message dictionary."""
        if not isinstance(req, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request"}
            }

        req_id = req.get("id")
        method = req.get("method")
        # In JSON-RPC 2.0, a request has an id (which can be number, string, or null).
        # A notification has NO 'id' key at all.
        is_notification = ("id" not in req)

        if is_notification:
            # Notifications expect no response and should not block the caller
            if method != "notifications/initialized":
                self.ensure_process_running()
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

        elif method == "server/discover":
            disc_req = {
                "jsonrpc": "2.0",
                "id": f"discover_{req_id}",
                "method": "tools/list",
                "params": {}
            }
            list_res = self.query_proc(disc_req, expected_id=disc_req["id"])
            if list_res and isinstance(list_res, dict):
                tools_list = list_res.get("result", {}).get("tools", [])
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
            resp = self.query_proc(req, expected_id=req_id)
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
            # Auto-bind active studio for actions other than list/set
            if tool_name not in ["list_roblox_studios", "set_active_studio"]:
                self.ensure_active_studio()

            resp = self.query_proc(req, expected_id=req_id)
            if resp is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "StudioMCP process did not respond to tools/call"}
                }

            if isinstance(resp, dict) and "result" in resp:
                content = resp["result"].get("content", [])
                if isinstance(content, list) and len(content) > 0:
                    err_text = content[0].get("text", "")
                    if "Not connected" in err_text or "No active studio" in err_text:
                        self.active_studio_set = False
                        # Retry once with active studio rebound
                        if tool_name not in ["list_roblox_studios", "set_active_studio"]:
                            if self.ensure_active_studio():
                                retry_resp = self.query_proc(req, expected_id=req_id)
                                if retry_resp is not None:
                                    return retry_resp

            return resp

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        else:
            resp = self.query_proc(req, expected_id=req_id)
            if resp is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": f"StudioMCP process did not respond to {method}"}
                }
            return resp

    def handle_message(self, req):
        """Handles incoming JSON-RPC payload, including batch requests (lists)."""
        if isinstance(req, list):
            if not req:
                return {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Request: empty batch"}
                }
            responses = []
            for item in req:
                resp = self.handle_single_message(item)
                if resp is not None:
                    responses.append(resp)
            return responses if responses else None
        return self.handle_single_message(req)

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
                except Exception as err:
                    req_id = req.get("id") if isinstance(req, dict) else None
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32603,
                            "message": f"Internal bridge error: {str(err)}"
                        }
                    }

                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
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
