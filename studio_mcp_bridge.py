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
    """Ensure UTF-8 I/O on Windows or any runtime environment with line buffering."""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
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

    versions_dir = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions")
    candidates = []
    if os.path.exists(versions_dir):
        for root, _, files in os.walk(versions_dir):
            for file in files:
                if file.lower() == "studiomcp.exe":
                    full_path = os.path.join(root, file)
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
            return False

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
            return True
        except Exception as e:
            sys.stderr.write(f"Error starting StudioMCP process: {e}\n")
            return False

    def ensure_process_alive(self):
        """Ensure StudioMCP.exe subprocess is running, restarting it if needed."""
        if self.proc is None or self.proc.poll() is not None:
            path = self.studio_mcp_path or find_studio_mcp_path()
            if path:
                self.active_studio_set = False
                if self.start_process():
                    self.init_proc()
                    return True
            return False
        return True

    def send_to_proc(self, msg):
        self.ensure_process_alive()
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            return False
        payload = json.dumps(msg) + "\n"
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def read_from_proc(self, expected_id=None):
        """
        Reads JSON-RPC output lines from StudioMCP.exe stdout.
        If expected_id is specified, continues reading (up to max_reads) until
        a response matching expected_id is found, ignoring notifications/mismatched IDs.
        """
        if not self.proc or not self.proc.stdout or self.proc.poll() is not None:
            return None

        max_reads = 20 if expected_id is not None else 1
        for _ in range(max_reads):
            if not self.proc or not self.proc.stdout or self.proc.poll() is not None:
                return None
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
                if not isinstance(msg, dict):
                    continue
                if expected_id is not None:
                    if msg.get("id") == expected_id:
                        return msg
                    continue
                return msg
            except Exception:
                pass
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
        self.init_res = self.read_from_proc(expected_id="__bridge_init__")
        self.send_to_proc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self.init_res

    def call_proc_tool(self, name, args, call_id):
        for attempt in range(5):
            self.ensure_process_alive()
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
                        if isinstance(first_item, dict):
                            err_text = first_item.get("text", "")
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
                result_dict = res["result"]
                if isinstance(result_dict, dict):
                    content = result_dict.get("content", [])
                    if isinstance(content, list) and len(content) > 0:
                        first_item = content[0]
                        if isinstance(first_item, dict):
                            txt = first_item.get("text", "{}")
                            data = json.loads(txt)
                            if isinstance(data, dict):
                                studios = data.get("studios", [])
                                if isinstance(studios, list) and len(studios) > 0:
                                    first_studio = studios[0]
                                    if isinstance(first_studio, dict):
                                        sid = first_studio.get("id")
                                        if sid:
                                            set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, "set")
                                            if (set_res and isinstance(set_res, dict) and
                                                    isinstance(set_res.get("result"), dict) and
                                                    not set_res["result"].get("isError", False)):
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
            disc_id = f"__bridge_discover_{req_id}"
            if self.send_to_proc({
                "jsonrpc": "2.0",
                "id": disc_id,
                "method": "tools/list",
                "params": {}
            }):
                list_res = self.read_from_proc(expected_id=disc_id)
                if list_res and isinstance(list_res, dict) and "result" in list_res:
                    tools_list = list_res["result"].get("tools", []) if isinstance(list_res["result"], dict) else []
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
                "error": {"code": -32603, "message": "Failed to query StudioMCP process for discover"}
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
                    first_item = content[0]
                    if isinstance(first_item, dict):
                        err_text = first_item.get("text", "")
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

            except (EOFError, ValueError, OSError):
                break
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
        if not bridge.start_process():
            sys.exit(1)
        bridge.init_proc()
        bridge.run()
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
