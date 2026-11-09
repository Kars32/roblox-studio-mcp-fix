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
    """Returns True if debug logging is enabled via STUDIO_MCP_DEBUG."""
    val = os.environ.get("STUDIO_MCP_DEBUG", "").strip().lower()
    return val in ("1", "true", "yes")


def log_debug(msg):
    """Outputs a debug message to stderr if STUDIO_MCP_DEBUG is active."""
    if is_debug_enabled():
        sys.stderr.write(f"[StudioMCPBridge] {msg}\n")
        sys.stderr.flush()


def reconfigure_io():
    """Ensure UTF-8 I/O on Windows or any runtime environment."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def find_studio_mcp_path():
    """
    Locates StudioMCP.exe via STUDIO_MCP_PATH env var or by searching Roblox Versions.
    Returns the executable with the latest modification time if multiple versions exist.
    """
    env_path = os.environ.get("STUDIO_MCP_PATH")
    if env_path and os.path.isfile(env_path):
        log_debug(f"Using STUDIO_MCP_PATH: {env_path}")
        return env_path

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
        log_debug(f"Discovered StudioMCP.exe at: {chosen}")
        return chosen

    log_debug("No StudioMCP.exe found in search locations.")
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
            sys.stderr.write("Error: Could not locate StudioMCP.exe in Roblox installation directories\n")
            sys.exit(1)

        log_debug(f"Spawning StudioMCP process: {self.studio_mcp_path}")
        self.proc = subprocess.Popen(
            [self.studio_mcp_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1
        )

    def ensure_process_running(self):
        """Ensures the StudioMCP subprocess is active; re-initializes if it exited."""
        if self.proc is None or self.proc.poll() is not None:
            log_debug("StudioMCP process is not running. Restarting...")
            self.active_studio_set = False
            self.start_process()
            self.init_proc()

    def send_to_proc(self, msg):
        self.ensure_process_running()
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            return False
        payload = json.dumps(msg) + "\n"
        log_debug(f"-> StudioMCP: {payload.strip()}")
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError) as e:
            log_debug(f"Error sending to StudioMCP: {e}")
            return False

    def read_from_proc(self, expected_id=None):
        """
        Reads lines from the StudioMCP subprocess.
        If expected_id is specified, continues reading until the matching response is found,
        forwarding or skipping any notifications/unrelated messages received in between.
        """
        if not self.proc or not self.proc.stdout or self.proc.poll() is not None:
            return None
        while True:
            try:
                line = self.proc.stdout.readline()
            except (OSError, ValueError) as e:
                log_debug(f"Error reading line from StudioMCP: {e}")
                return None
            if not line:
                log_debug("EOF encountered reading from StudioMCP")
                return None
            line_str = line.strip()
            if not line_str:
                continue
            log_debug(f"<- StudioMCP: {line_str}")
            try:
                data = json.loads(line_str)
            except Exception as e:
                log_debug(f"Failed to parse line from StudioMCP as JSON: {e}")
                continue

            if not isinstance(data, dict):
                continue

            # If expected_id is provided, check if this is the matching response
            if expected_id is not None:
                msg_id = data.get("id")
                if msg_id == expected_id:
                    return data
                # If it's a notification from the server to client, forward it to stdout
                if "method" in data and ("id" not in data or data.get("id") is None):
                    sys.stdout.write(line_str + "\n")
                    sys.stdout.flush()
                # Otherwise, it might be an unsolicited response or mismatched message; keep reading
                continue

            return data

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
        self.init_res = self.read_from_proc(expected_id=req_id)
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
                        first_content = content[0]
                        err_text = first_content.get("text", "") if isinstance(first_content, dict) else ""
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
                    first_content = content[0]
                    txt = first_content.get("text", "{}") if isinstance(first_content, dict) else "{}"
                    data = json.loads(txt) if isinstance(txt, str) else {}
                    studios = data.get("studios", []) if isinstance(data, dict) else []
                    if studios and isinstance(studios, list) and len(studios) > 0:
                        first_studio = studios[0]
                        sid = first_studio.get("id") if isinstance(first_studio, dict) else None
                        if sid is not None:
                            set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, "set")
                            if set_res and not set_res.get("result", {}).get("isError", False) and "error" not in set_res:
                                self.active_studio_set = True
                                return True
        except Exception as e:
            log_debug(f"Exception in ensure_active_studio: {e}")
        return False

    def handle_message(self, req):
        if not isinstance(req, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: message must be an object"}
            }

        req_id = req.get("id")
        method = req.get("method")
        has_id = ("id" in req and req_id is not None)

        # Handle client responses or notifications without method
        if method is None:
            if has_id or "result" in req or "error" in req:
                # This is a response from the client; forward to subprocess if alive, no response expected
                self.send_to_proc(req)
                return None
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32600, "message": "Invalid Request: method is missing"}
            }

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
            disc_id = f"__bridge_discover_{req_id}"
            if self.send_to_proc({
                "jsonrpc": "2.0",
                "id": disc_id,
                "method": "tools/list",
                "params": {}
            }):
                list_res = self.read_from_proc(expected_id=disc_id)
                if list_res and "result" in list_res:
                    tools_list = list_res.get("result", {}).get("tools", [])
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "tools": tools_list,
                            "serverInfo": {"name": "RobloxStudio", "version": "1.2.0"}
                        }
                    }
                elif list_res and "error" in list_res:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": list_res["error"]
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

            if isinstance(resp, dict) and "result" in resp:
                content = resp["result"].get("content", [])
                if isinstance(content, list) and len(content) > 0:
                    first_content = content[0]
                    err_text = first_content.get("text", "") if isinstance(first_content, dict) else ""
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

                log_debug(f"-> Bridge from Host: {line_str}")
                try:
                    parsed = json.loads(line_str)
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

                if isinstance(parsed, list):
                    if not parsed:
                        err_resp = {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32600, "message": "Invalid Request: empty batch"}
                        }
                        sys.stdout.write(json.dumps(err_resp) + "\n")
                        sys.stdout.flush()
                        continue

                    batch_responses = []
                    for item in parsed:
                        resp = self.handle_message(item)
                        if resp is not None:
                            batch_responses.append(resp)
                    if batch_responses:
                        sys.stdout.write(json.dumps(batch_responses) + "\n")
                        sys.stdout.flush()
                else:
                    resp = self.handle_message(parsed)
                    if resp is not None:
                        log_debug(f"<- Bridge to Host: {json.dumps(resp)}")
                        sys.stdout.write(json.dumps(resp) + "\n")
                        sys.stdout.flush()

            except (KeyboardInterrupt, SystemExit):
                break
            except Exception as e:
                log_debug(f"Unexpected exception in bridge main loop: {e}")

    def close(self):
        if self.proc:
            if self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=1)
            if self.proc.stdout:
                try:
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
