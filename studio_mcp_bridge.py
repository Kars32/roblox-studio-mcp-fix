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
    """Check if debug logging is enabled via STUDIO_MCP_DEBUG."""
    val = os.environ.get("STUDIO_MCP_DEBUG", "").strip().lower()
    return val in ("1", "true", "yes")


def log_debug(msg):
    """Write debug output to sys.stderr if debug is enabled."""
    if is_debug_enabled():
        sys.stderr.write(f"[StudioMCP-Bridge] {msg}\n")
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
    Optimized to skip deep asset subdirectories under version folders.
    """
    env_path = os.environ.get("STUDIO_MCP_PATH")
    if env_path and os.path.isfile(env_path):
        log_debug(f"Using STUDIO_MCP_PATH override: {env_path}")
        return env_path

    versions_dir = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions")
    candidates = []
    if os.path.exists(versions_dir):
        try:
            entries = os.listdir(versions_dir)
        except OSError:
            entries = []

        for entry in entries:
            version_folder = os.path.join(versions_dir, entry)
            if not os.path.isdir(version_folder):
                continue
            # StudioMCP.exe is installed directly at the root of a version folder
            candidate = os.path.join(version_folder, "StudioMCP.exe")
            if os.path.isfile(candidate):
                try:
                    mtime = os.path.getmtime(candidate)
                    candidates.append((mtime, candidate))
                except OSError:
                    candidates.append((0, candidate))
            else:
                # Fallback to shallow subfolder search (max 1 level) in case of nested layout
                try:
                    for sub in os.listdir(version_folder):
                        sub_path = os.path.join(version_folder, sub)
                        if os.path.isdir(sub_path):
                            nested = os.path.join(sub_path, "StudioMCP.exe")
                            if os.path.isfile(nested):
                                try:
                                    mtime = os.path.getmtime(nested)
                                    candidates.append((mtime, nested))
                                except OSError:
                                    candidates.append((0, nested))
                except OSError:
                    pass

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        log_debug(f"Found StudioMCP candidate: {candidates[0][1]}")
        return candidates[0][1]

    log_debug("Could not find StudioMCP.exe in Roblox Versions directory.")
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
            err_msg = "Error: Could not locate StudioMCP.exe in %LOCALAPPDATA%\\Roblox\\Versions\n"
            sys.stderr.write(err_msg)
            sys.stderr.flush()
            raise FileNotFoundError(err_msg.strip())

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

    def send_to_proc(self, msg):
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            log_debug("send_to_proc failed: process not running or stdin unavailable")
            return False
        payload = json.dumps(msg) + "\n"
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError) as e:
            log_debug(f"send_to_proc write error: {e}")
            return False

    def read_from_proc(self, expected_id=None):
        """
        Reads lines from the subprocess until a valid JSON-RPC message is parsed.
        If expected_id is provided, skips notifications or responses with mismatched IDs
        until the response matching expected_id is found or EOF/error occurs.
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

            if expected_id is not None:
                # If expected_id is specified, check if this message matches
                msg_id = msg.get("id")
                if msg_id == expected_id:
                    return msg
                # If it's a notification from the subprocess, ignore or log it
                if "id" not in msg or msg_id is None:
                    log_debug(f"Subprocess notification received while waiting for {expected_id}: {msg.get('method')}")
                    continue
                # ID mismatch (unexpected response)
                log_debug(f"Mismatched id received: got {msg_id}, expected {expected_id}")
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
                if "result" in res and isinstance(res["result"], dict):
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

    def ensure_active_studio(self, force_refresh=False):
        if self.active_studio_set and not force_refresh:
            return True
        try:
            res = self.call_proc_tool("list_roblox_studios", {}, "list")
            if res and "result" in res and isinstance(res["result"], dict):
                content = res["result"].get("content", [])
                if content and isinstance(content, list) and len(content) > 0:
                    txt = content[0].get("text", "{}")
                    data = json.loads(txt)
                    studios = data.get("studios", [])
                    if studios and isinstance(studios, list) and len(studios) > 0:
                        # Iterate through available studios until one successfully activates
                        for studio in studios:
                            if not isinstance(studio, dict):
                                continue
                            sid = studio.get("id")
                            if sid:
                                set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, "set")
                                if set_res and not set_res.get("result", {}).get("isError", False):
                                    self.active_studio_set = True
                                    log_debug(f"Successfully bound active Studio session: {sid}")
                                    return True
        except Exception as e:
            log_debug(f"ensure_active_studio error: {e}")
        return False

    def handle_message(self, req):
        if not isinstance(req, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: request must be an object"}
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
            disc_id = "__bridge_discover__"
            if self.send_to_proc({
                "jsonrpc": "2.0",
                "id": disc_id,
                "method": "tools/list",
                "params": {}
            }):
                list_res = self.read_from_proc(expected_id=disc_id)
                if list_res is None or "error" in list_res:
                    err_details = list_res.get("error") if list_res else {"code": -32603, "message": "StudioMCP process did not respond"}
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": err_details
                    }
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

            if isinstance(resp, dict) and "result" in resp and isinstance(resp["result"], dict):
                content = resp["result"].get("content", [])
                if isinstance(content, list) and len(content) > 0:
                    err_text = content[0].get("text", "")
                    if "Not connected" in err_text or "No active studio" in err_text:
                        self.active_studio_set = False
                        # Attempt one automatic rebind and retry if it was not an explicit list/set call
                        if tool_name not in ["list_roblox_studios", "set_active_studio"]:
                            log_debug("Active studio was disconnected. Attempting rebind and retry...")
                            if self.ensure_active_studio(force_refresh=True):
                                if self.send_to_proc(req):
                                    retry_resp = self.read_from_proc(expected_id=req_id)
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
                    sys.stdout.write(json.dumps(err_resp) + "\n")
                    sys.stdout.flush()
                    continue

                req_id = req.get("id") if isinstance(req, dict) else None
                try:
                    resp = self.handle_message(req)
                    if resp is not None:
                        sys.stdout.write(json.dumps(resp) + "\n")
                        sys.stdout.flush()
                except Exception as e:
                    log_debug(f"Exception handling message {req}: {e}")
                    if req_id is not None:
                        err_resp = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32603,
                                "message": f"Internal bridge error: {str(e)}"
                            }
                        }
                        sys.stdout.write(json.dumps(err_resp) + "\n")
                        sys.stdout.flush()

            except (KeyboardInterrupt, SystemExit):
                break
            except Exception as e:
                log_debug(f"Unexpected loop exception: {e}")

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
    except Exception as e:
        log_debug(f"Fatal error in main: {e}")
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
