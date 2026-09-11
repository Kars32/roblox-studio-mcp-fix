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
import threading
import queue


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

    possible_dirs = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        possible_dirs.append(os.path.join(local_app_data, "Roblox", "Versions"))

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        possible_dirs.append(os.path.join(user_profile, "AppData", "Local", "Roblox", "Versions"))

    expanded_path = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions")
    if expanded_path not in possible_dirs:
        possible_dirs.append(expanded_path)

    candidates = []
    for versions_dir in possible_dirs:
        if os.path.exists(versions_dir):
            for root, _, files in os.walk(versions_dir):
                for f in files:
                    if f.lower() == "studiomcp.exe":
                        full_path = os.path.join(root, f)
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
        self.msg_queue = queue.Queue()
        self.reader_thread = None
        self._stop_reader = False

        if self.proc is not None and getattr(self.proc, "stdout", None) is not None:
            self._start_reader_thread()

    def _start_reader_thread(self):
        self._stop_reader = False
        self.reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.reader_thread.start()

    def _read_loop(self):
        while not self._stop_reader and self.proc and self.proc.stdout and self.proc.poll() is None:
            try:
                line = self.proc.stdout.readline()
                if not line or not isinstance(line, str):
                    break
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    self.msg_queue.put(data)
                except Exception:
                    pass
            except Exception:
                break
        self.msg_queue.put(None)

    def is_process_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def ensure_process_running(self):
        if not self.is_process_alive():
            if self.studio_mcp_path or os.environ.get("STUDIO_MCP_PATH") or find_studio_mcp_path():
                self.close()
                self.start_process()
                self.init_proc()
                self.active_studio_set = False
                return True
            return False
        return True

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
        self.msg_queue = queue.Queue()
        self._start_reader_thread()

    def send_to_proc(self, msg):
        self.ensure_process_running()
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            return False
        payload = json.dumps(msg) + "\n"
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def read_from_proc(self, target_id=None, timeout=5.0):
        if self.reader_thread and self.reader_thread.is_alive():
            start_time = time.time()
            while True:
                remaining = timeout - (time.time() - start_time) if timeout is not None else None
                if remaining is not None and remaining <= 0:
                    return None
                try:
                    msg = self.msg_queue.get(timeout=remaining if remaining is not None else 0.1)
                except queue.Empty:
                    return None

                if msg is None:
                    return None

                if isinstance(msg, dict):
                    if "id" not in msg or msg["id"] is None:
                        sys.stdout.write(json.dumps(msg) + "\n")
                        sys.stdout.flush()
                        continue

                    if target_id is None or msg.get("id") == target_id:
                        return msg
                    continue
            return None

        # Fallback for environments without an active reader thread (e.g. mocked proc in unit tests)
        if not self.proc or not self.proc.stdout or self.proc.poll() is not None:
            return None
        while True:
            try:
                line = self.proc.stdout.readline()
            except (OSError, ValueError):
                return None
            if not line or not isinstance(line, str):
                return None
            line_str = line.strip()
            if not line_str:
                continue
            try:
                msg = json.loads(line_str)
                if isinstance(msg, dict):
                    if ("id" not in msg or msg["id"] is None) and target_id is not None:
                        sys.stdout.write(json.dumps(msg) + "\n")
                        sys.stdout.flush()
                        continue
                    if target_id is None or msg.get("id") == target_id:
                        return msg
                    continue
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
                "clientInfo": {"name": "antigravity_bridge", "version": "1.2.0"}
            }
        }):
            return None
        self.init_res = self.read_from_proc(target_id="__bridge_init__")
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
            res = self.read_from_proc(target_id=req_id)
            if res and isinstance(res, dict) and res.get("id") == req_id:
                if "result" in res:
                    content = res["result"].get("content", [])
                    is_error = res["result"].get("isError", False)
                    if is_error and isinstance(content, list) and len(content) > 0:
                        err_text = content[0].get("text", "") if isinstance(content[0], dict) else ""
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
                content = res["result"].get("content", [])
                if isinstance(content, list) and len(content) > 0:
                    first_item = content[0]
                    txt = first_item.get("text", "{}") if isinstance(first_item, dict) else "{}"
                    data = json.loads(txt) if isinstance(txt, str) else txt

                    studios = []
                    if isinstance(data, dict):
                        studios = data.get("studios", [])
                    elif isinstance(data, list):
                        studios = data

                    if studios and isinstance(studios, list) and len(studios) > 0:
                        first_studio = studios[0]
                        sid = None
                        if isinstance(first_studio, dict):
                            sid = first_studio.get("id") or first_studio.get("studio_id") or first_studio.get("studioId")
                        elif isinstance(first_studio, (str, int)):
                            sid = first_studio

                        if sid is not None:
                            set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, "set")
                            if set_res and isinstance(set_res, dict) and not set_res.get("result", {}).get("isError", False):
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
            if self.send_to_proc({
                "jsonrpc": "2.0",
                "id": "list_discover",
                "method": "tools/list",
                "params": {}
            }):
                list_res = self.read_from_proc(target_id="list_discover")
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
            resp = self.read_from_proc(target_id=req_id)
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
            if tool_name not in ["list_roblox_studios", "set_active_studio"]:
                self.ensure_active_studio()

            if not self.send_to_proc(req):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "Failed to send tool call to StudioMCP process"}
                }
            resp = self.read_from_proc(target_id=req_id)
            if resp is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "StudioMCP process did not respond to tools/call"}
                }

            if isinstance(resp, dict) and "result" in resp:
                content = resp["result"].get("content", [])
                is_error = resp["result"].get("isError", False)
                if is_error and isinstance(content, list) and len(content) > 0:
                    err_text = content[0].get("text", "") if isinstance(content[0], dict) else ""
                    if "Not connected" in err_text or "No active studio" in err_text:
                        self.active_studio_set = False
                elif not is_error and tool_name == "set_active_studio":
                    self.active_studio_set = True

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
            resp = self.read_from_proc(target_id=req_id)
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

                resp = self.handle_message(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()

            except (KeyboardInterrupt, SystemExit):
                break
            except Exception:
                pass

    def close(self):
        self._stop_reader = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self.proc.kill()
                except OSError:
                    pass
        self.proc = None


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
