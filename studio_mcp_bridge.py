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


def debug_log(*args):
    """Write debug logs to stderr if debug mode is active."""
    if is_debug_enabled():
        msg = " ".join(str(a) for a in args)
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
    Optimized to check immediate version directories first before recursive walk.
    """
    env_path = os.environ.get("STUDIO_MCP_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    versions_dir = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions")
    if not os.path.exists(versions_dir) and not sys.platform.startswith("win"):
        # Fallback for non-Windows testing / alternate environments
        user_home = os.path.expanduser("~")
        fallback_dir = os.path.join(user_home, "AppData", "Local", "Roblox", "Versions")
        if os.path.exists(fallback_dir):
            versions_dir = fallback_dir

    candidates = []
    if os.path.exists(versions_dir):
        try:
            # Fast pass: Roblox typically installs in Versions/version-<hash>/StudioMCP.exe
            with os.scandir(versions_dir) as entries:
                for entry in entries:
                    if entry.is_dir():
                        target = os.path.join(entry.path, "StudioMCP.exe")
                        if os.path.isfile(target):
                            try:
                                mtime = os.path.getmtime(target)
                                candidates.append((mtime, target))
                            except OSError:
                                candidates.append((0, target))
        except OSError:
            pass

        # Fallback pass if not found in immediate subdirectories
        if not candidates:
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
        self.pending_responses = {}
        self.buffered_notifications = []

    def start_process(self):
        if not self.studio_mcp_path:
            self.studio_mcp_path = find_studio_mcp_path()

        if not self.studio_mcp_path:
            sys.stderr.write("Error: Could not locate StudioMCP.exe in %LOCALAPPDATA%\\Roblox\\Versions\n")
            sys.exit(1)

        debug_log(f"Starting StudioMCP process from {self.studio_mcp_path}")
        self.proc = subprocess.Popen(
            [self.studio_mcp_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1
        )
        self.pending_responses.clear()
        self.buffered_notifications.clear()

    def ensure_process_running(self):
        """Ensures the StudioMCP process is running, restarting it if necessary."""
        if self.proc is None or self.proc.poll() is not None:
            debug_log("StudioMCP process is not running. Restarting...")
            self.start_process()
            self.init_proc()
            self.active_studio_set = False
            return True
        return False

    def send_to_proc(self, msg):
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            debug_log("Cannot send message: process is not running")
            return False
        payload = json.dumps(msg) + "\n"
        debug_log(f"--> StudioMCP: {payload.strip()}")
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError) as e:
            debug_log(f"Send error to StudioMCP: {e}")
            return False

    def read_from_proc(self, expected_id=None, max_reads=50):
        """
        Reads lines from the subprocess until a message matching expected_id is found,
        buffering out-of-order responses and notifications.
        """
        if expected_id is not None and expected_id in self.pending_responses:
            debug_log(f"Returning cached response for id: {expected_id}")
            return self.pending_responses.pop(expected_id)

        if not self.proc or not self.proc.stdout or self.proc.poll() is not None:
            return None

        reads = 0
        while reads < max_reads:
            reads += 1
            try:
                line = self.proc.stdout.readline()
            except (OSError, ValueError) as e:
                debug_log(f"Error reading from StudioMCP: {e}")
                return None

            if not line:
                debug_log("EOF received from StudioMCP stdout")
                return None

            line_str = line.strip()
            if not line_str:
                continue

            try:
                msg = json.loads(line_str)
            except Exception as e:
                debug_log(f"Malformed JSON from StudioMCP: {line_str} ({e})")
                continue

            debug_log(f"<-- StudioMCP: {line_str}")

            if not isinstance(msg, dict):
                continue

            msg_id = msg.get("id")
            if msg_id is None:
                # Subprocess notification
                self.buffered_notifications.append(msg)
                if expected_id is None:
                    return msg
                continue

            if expected_id is None or msg_id == expected_id:
                return msg
            else:
                self.pending_responses[msg_id] = msg

        debug_log(f"Exceeded max_reads ({max_reads}) waiting for id: {expected_id}")
        return None

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
                # Attempt process recovery if dead
                if self.ensure_process_running():
                    continue
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
                    data = json.loads(txt)
                    studios = data.get("studios", [])
                    if studios and isinstance(studios, list) and len(studios) > 0:
                        sid = studios[0].get("id")
                        if sid:
                            set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, "set")
                            if set_res and not set_res.get("result", {}).get("isError", False):
                                self.active_studio_set = True
                                return True
        except Exception as e:
            debug_log(f"Error in ensure_active_studio: {e}")
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
            disc_id = f"__bridge_discover_{req_id}"
            if self.send_to_proc({
                "jsonrpc": "2.0",
                "id": disc_id,
                "method": "tools/list",
                "params": {}
            }):
                list_res = self.read_from_proc(expected_id=disc_id)
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
                # Try reviving process once
                if self.ensure_process_running() and self.send_to_proc(req):
                    pass
                else:
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
                # Try reviving process once
                if self.ensure_process_running():
                    if tool_name not in ["list_roblox_studios", "set_active_studio"]:
                        self.ensure_active_studio()
                    if not self.send_to_proc(req):
                        return {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32603, "message": "Failed to send tool call to StudioMCP process"}
                        }
                else:
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
                    err_text = content[0].get("text", "")
                    if "Not connected" in err_text or "No active studio" in err_text:
                        self.active_studio_set = False

            return resp

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        else:
            if not self.send_to_proc(req):
                if self.ensure_process_running() and self.send_to_proc(req):
                    pass
                else:
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

                debug_log(f"<-- Host: {line_str}")
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

                # Forward any buffered server notifications first
                while self.buffered_notifications:
                    notif = self.buffered_notifications.pop(0)
                    debug_log(f"--> Host (buffered notif): {json.dumps(notif)}")
                    sys.stdout.write(json.dumps(notif) + "\n")
                    sys.stdout.flush()

                resp = self.handle_message(req)
                if resp is not None:
                    out = json.dumps(resp)
                    debug_log(f"--> Host: {out}")
                    sys.stdout.write(out + "\n")
                    sys.stdout.flush()

                # Also flush notifications buffered during handle_message
                while self.buffered_notifications:
                    notif = self.buffered_notifications.pop(0)
                    debug_log(f"--> Host (buffered notif): {json.dumps(notif)}")
                    sys.stdout.write(json.dumps(notif) + "\n")
                    sys.stdout.flush()

            except (KeyboardInterrupt, SystemExit):
                break
            except Exception as e:
                debug_log(f"Unexpected error in run loop: {e}")

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
