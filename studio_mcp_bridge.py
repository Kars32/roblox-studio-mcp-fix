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


def is_debug_enabled():
    """Checks whether debug logging is enabled via STUDIO_MCP_DEBUG."""
    val = os.environ.get("STUDIO_MCP_DEBUG", "").strip().lower()
    return val in ("1", "true", "yes")


def log_debug(msg):
    """Outputs a debug message to sys.stderr if STUDIO_MCP_DEBUG is active."""
    if is_debug_enabled():
        sys.stderr.write(f"[StudioMCPBridge] {msg}\n")
        sys.stderr.flush()


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
    if env_path:
        env_path = env_path.strip().strip('"').strip("'")
        if os.path.isfile(env_path):
            log_debug(f"Using StudioMCP path from environment: {env_path}")
            return env_path

    # Search standard Roblox Versions directories
    search_dirs = [
        os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions"),
        os.path.expandvars(r"%PROGRAMFILES%\Roblox\Versions"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Roblox\Versions"),
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
        chosen = candidates[0][1]
        log_debug(f"Discovered StudioMCP path: {chosen}")
        return chosen

    log_debug("Could not locate StudioMCP.exe in any search directory.")
    return None


class StudioMCPBridge:
    def __init__(self, studio_mcp_path=None, proc=None):
        self.studio_mcp_path = studio_mcp_path
        self.proc = proc
        self.active_studio_set = False
        self.init_res = None
        self._pending_responses = {}

    def start_process(self):
        if not self.studio_mcp_path:
            self.studio_mcp_path = find_studio_mcp_path()

        if not self.studio_mcp_path:
            sys.stderr.write("Error: Could not locate StudioMCP.exe in %LOCALAPPDATA%\\Roblox\\Versions\n")
            sys.exit(1)

        log_debug(f"Starting StudioMCP process: {self.studio_mcp_path}")
        self.proc = subprocess.Popen(
            [self.studio_mcp_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1
        )
        self._pending_responses.clear()

    def send_to_proc(self, msg):
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            log_debug("send_to_proc failed: process not running")
            return False
        payload = json.dumps(msg) + "\n"
        try:
            log_debug(f"Sending to proc: {payload.strip()}")
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError) as e:
            log_debug(f"send_to_proc exception: {e}")
            return False

    def read_from_proc(self, expected_id=None):
        """
        Reads a JSON response from the subprocess.
        If expected_id is specified, buffers other responses and notifications
        until a response with expected_id is found or EOF is reached.
        """
        if expected_id is not None and expected_id in self._pending_responses:
            return self._pending_responses.pop(expected_id)

        if not self.proc or not self.proc.stdout or self.proc.poll() is not None:
            return None

        while True:
            try:
                line = self.proc.stdout.readline()
            except (OSError, ValueError) as e:
                log_debug(f"read_from_proc read error: {e}")
                return None

            if not line:
                log_debug("read_from_proc reached EOF")
                return None

            line_str = line.strip()
            if not line_str:
                continue

            try:
                msg = json.loads(line_str)
            except Exception as e:
                log_debug(f"read_from_proc json parse error: {e} for line: {line_str}")
                continue

            log_debug(f"Received from proc: {line_str}")

            if not isinstance(msg, dict):
                continue

            msg_id = msg.get("id")

            if expected_id is None:
                return msg

            if msg_id == expected_id:
                return msg
            elif msg_id is not None:
                # Buffer response for other request ID
                self._pending_responses[msg_id] = msg
            else:
                # Server-sent notification from StudioMCP
                # Forward directly to stdout if it's a notification
                try:
                    sys.stdout.write(json.dumps(msg) + "\n")
                    sys.stdout.flush()
                except Exception:
                    pass

    def init_proc(self):
        init_id = "__bridge_init__"
        if not self.send_to_proc({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "clientInfo": {"name": "antigravity_bridge", "version": "1.2.0"}
            }
        }):
            return None
        self.init_res = self.read_from_proc(expected_id=init_id)
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
            res = self.read_from_proc(expected_id=req_id)
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
                    try:
                        data = json.loads(txt)
                    except Exception:
                        data = {}

                    studios = None
                    if isinstance(data, dict):
                        studios = data.get("studios", [])
                    elif isinstance(data, list):
                        studios = data

                    if studios and isinstance(studios, list) and len(studios) > 0:
                        first_studio = studios[0]
                        sid = None
                        if isinstance(first_studio, dict):
                            sid = first_studio.get("id") or first_studio.get("studio_id")
                        elif isinstance(first_studio, (str, int)):
                            sid = str(first_studio)

                        if sid:
                            set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, "set")
                            if set_res and not set_res.get("result", {}).get("isError", False) and "error" not in set_res:
                                log_debug(f"Successfully bound active studio: {sid}")
                                self.active_studio_set = True
                                return True
        except Exception as e:
            log_debug(f"ensure_active_studio exception: {e}")
        return False

    def handle_message(self, req):
        if not isinstance(req, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: Expected JSON object"}
            }

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
            discover_id = f"list_discover_{req_id}"
            if self.send_to_proc({
                "jsonrpc": "2.0",
                "id": discover_id,
                "method": "tools/list",
                "params": {}
            }):
                list_res = self.read_from_proc(expected_id=discover_id)
                tools_list = list_res.get("result", {}).get("tools", []) if (list_res and isinstance(list_res, dict)) else []
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

            if isinstance(resp, dict):
                if "result" in resp:
                    content = resp["result"].get("content", [])
                    if isinstance(content, list) and len(content) > 0:
                        err_text = content[0].get("text", "")
                        if "Not connected" in err_text or "No active studio" in err_text:
                            self.active_studio_set = False
                elif "error" in resp:
                    err_msg = resp["error"].get("message", "")
                    if "Not connected" in err_msg or "No active studio" in err_msg:
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
                    payload = json.loads(line_str)
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

                # JSON-RPC 2.0 batch support
                if isinstance(payload, list):
                    if len(payload) == 0:
                        batch_err = {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32600, "message": "Invalid Request: empty batch"}
                        }
                        sys.stdout.write(json.dumps(batch_err) + "\n")
                        sys.stdout.flush()
                    else:
                        batch_resps = []
                        for item in payload:
                            item_resp = self.handle_message(item)
                            if item_resp is not None:
                                batch_resps.append(item_resp)
                        if batch_resps:
                            sys.stdout.write(json.dumps(batch_resps) + "\n")
                            sys.stdout.flush()
                else:
                    resp = self.handle_message(payload)
                    if resp is not None:
                        sys.stdout.write(json.dumps(resp) + "\n")
                        sys.stdout.flush()

            except (KeyboardInterrupt, SystemExit):
                break
            except Exception as e:
                log_debug(f"Unhandled loop error: {e}")

    def close(self):
        log_debug("Closing StudioMCPBridge...")
        if self.proc:
            try:
                if self.proc.stdin and not self.proc.stdin.closed:
                    self.proc.stdin.close()
            except Exception:
                pass

            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()

            try:
                if self.proc.stdout and not self.proc.stdout.closed:
                    self.proc.stdout.close()
            except Exception:
                pass


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
