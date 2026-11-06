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


def is_debug():
    """Check if debug logging is enabled via STUDIO_MCP_DEBUG."""
    return os.environ.get("STUDIO_MCP_DEBUG", "").strip().lower() in ("1", "true", "yes")


def debug_log(msg):
    """Write debug messages to stderr without interfering with JSON-RPC on stdout."""
    if is_debug():
        sys.stderr.write(f"[STUDIO_MCP_DEBUG] {msg}\n")
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
    if env_path and os.path.isfile(env_path):
        debug_log(f"Using StudioMCP path from STUDIO_MCP_PATH: {env_path}")
        return env_path

    versions_dir = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions")
    candidates = []
    if os.path.exists(versions_dir):
        # Fast path: check direct children of Versions (typical Roblox folder structure)
        try:
            for entry in os.listdir(versions_dir):
                candidate_path = os.path.join(versions_dir, entry, "StudioMCP.exe")
                if os.path.isfile(candidate_path):
                    try:
                        mtime = os.path.getmtime(candidate_path)
                        candidates.append((mtime, candidate_path))
                    except OSError:
                        candidates.append((0, candidate_path))
        except OSError:
            pass

        # Fallback to bounded directory walk if direct search didn't find any
        if not candidates:
            for root, dirs, files in os.walk(versions_dir):
                # Skip heavy content/asset directories to optimize search time
                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in ("content", "builtinplugins", "plugins", "shaders", "textures", "models", "docs")
                ]
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
        debug_log(f"Found StudioMCP executable: {chosen}")
        return chosen

    debug_log("StudioMCP.exe could not be found.")
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

        debug_log(f"Starting StudioMCP process: {self.studio_mcp_path}")
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
            debug_log("Cannot send to proc: process not running or stdin unavailable")
            return False
        payload = json.dumps(msg) + "\n"
        debug_log(f"-> Proc: {payload.strip()}")
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError) as e:
            debug_log(f"Error writing to proc stdin: {e}")
            return False

    def read_from_proc(self):
        """
        Reads next valid JSON-RPC message from subprocess.
        If a server notification (message without 'id') is encountered, it is forwarded
        directly to sys.stdout to prevent losing server notifications.
        """
        if not self.proc or not self.proc.stdout or self.proc.poll() is not None:
            return None
        while True:
            try:
                line = self.proc.stdout.readline()
            except (OSError, ValueError) as e:
                debug_log(f"Error reading from proc stdout: {e}")
                return None
            if not line:
                return None
            line_str = line.strip()
            if not line_str:
                continue
            debug_log(f"<- Proc: {line_str}")
            try:
                parsed = json.loads(line_str)
                # If subprocess sent an unprompted notification (has method, no id), forward to client
                if isinstance(parsed, dict) and "id" not in parsed and "method" in parsed:
                    sys.stdout.write(json.dumps(parsed) + "\n")
                    sys.stdout.flush()
                    continue
                return parsed
            except Exception as e:
                debug_log(f"Failed to parse JSON from proc: {e}")
                pass

    def read_matching_from_proc(self, expected_id, timeout=5.0):
        """
        Reads from subprocess until a response with the expected_id is received
        or until timeout/EOF occurs.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            msg = self.read_from_proc()
            if msg is None:
                return None
            if isinstance(msg, dict) and msg.get("id") == expected_id:
                return msg
            # If it's a response with a different ID (stray/late response), keep listening
            debug_log(f"Ignoring mismatched response id: {msg.get('id')} (expected {expected_id})")
        return None

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
        }):
            return None
        self.init_res = self.read_matching_from_proc(req_id)
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
            res = self.read_matching_from_proc(req_id)
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
            if res and isinstance(res, dict) and "result" in res:
                if res["result"].get("isError", False):
                    return False
                content = res["result"].get("content", [])
                if content and isinstance(content, list) and len(content) > 0:
                    txt = content[0].get("text", "{}")
                    data = json.loads(txt)
                    studios = data.get("studios", [])
                    if studios and isinstance(studios, list) and len(studios) > 0:
                        sid = studios[0].get("id")
                        if sid:
                            set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, "set")
                            if set_res and isinstance(set_res, dict) and "result" in set_res:
                                if not set_res["result"].get("isError", False):
                                    self.active_studio_set = True
                                    debug_log(f"Successfully bound active studio: {sid}")
                                    return True
        except Exception as e:
            debug_log(f"Exception during ensure_active_studio: {e}")
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
            discover_id = "list_discover"
            if self.send_to_proc({
                "jsonrpc": "2.0",
                "id": discover_id,
                "method": "tools/list",
                "params": {}
            }):
                list_res = self.read_matching_from_proc(discover_id)
                if list_res is None:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": "StudioMCP process did not respond to discovery probe"}
                    }
                tools_list = list_res.get("result", {}).get("tools", []) if isinstance(list_res, dict) else []
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
            resp = self.read_matching_from_proc(req_id)
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
            is_studio_tool = tool_name not in ["list_roblox_studios", "set_active_studio"]

            # Auto-bind active studio for actions other than list/set
            if is_studio_tool:
                self.ensure_active_studio()

            if not self.send_to_proc(req):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "Failed to send tool call to StudioMCP process"}
                }
            resp = self.read_matching_from_proc(req_id)
            if resp is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "StudioMCP process did not respond to tools/call"}
                }

            # Check if Studio session disconnected
            if is_studio_tool and isinstance(resp, dict) and "result" in resp:
                content = resp["result"].get("content", [])
                if isinstance(content, list) and len(content) > 0:
                    err_text = content[0].get("text", "")
                    if "Not connected" in err_text or "No active studio" in err_text:
                        debug_log(f"Disconnected response detected in tools/call: {err_text}. Attempting re-bind and retry...")
                        self.active_studio_set = False
                        # Attempt one retry after re-binding
                        if self.ensure_active_studio():
                            if self.send_to_proc(req):
                                retry_resp = self.read_matching_from_proc(req_id)
                                if retry_resp is not None:
                                    return retry_resp

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
            resp = self.read_matching_from_proc(req_id)
            if resp is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": f"StudioMCP process did not respond to {method}"}
                }
            return resp

    def run(self):
        debug_log("StudioMCPBridge running and listening on stdin...")
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    debug_log("EOF on stdin, shutting down bridge.")
                    break
                line_str = line.strip()
                if not line_str:
                    continue

                debug_log(f"Host -> Bridge: {line_str}")
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

                if isinstance(payload, list):
                    # Handle batch JSON-RPC request
                    responses = []
                    for req in payload:
                        r = self.handle_message(req)
                        if r is not None:
                            responses.append(r)
                    if responses:
                        sys.stdout.write(json.dumps(responses) + "\n")
                        sys.stdout.flush()
                else:
                    resp = self.handle_message(payload)
                    if resp is not None:
                        debug_log(f"Bridge -> Host: {json.dumps(resp)}")
                        sys.stdout.write(json.dumps(resp) + "\n")
                        sys.stdout.flush()

            except (KeyboardInterrupt, SystemExit):
                break
            except Exception as e:
                debug_log(f"Unexpected error in run loop: {e}")
                pass

    def close(self):
        debug_log("Closing StudioMCPBridge...")
        if self.proc and self.proc.poll() is None:
            if self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
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
