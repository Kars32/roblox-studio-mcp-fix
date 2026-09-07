"""
Roblox Studio MCP Compatibility Bridge (v1.1.0)
Fixes stdio deadlocks, handles discovery probes, and auto-binds active Studio sessions.
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
    """Locate StudioMCP.exe dynamically or via environment override."""
    env_path = os.environ.get("STUDIO_MCP_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    versions_dir = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\Versions")
    if os.path.exists(versions_dir):
        for root, _, files in os.walk(versions_dir):
            if "StudioMCP.exe" in files:
                return os.path.join(root, "StudioMCP.exe")

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
        if not self.proc or not self.proc.stdin:
            return
        payload = json.dumps(msg) + "\n"
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()

    def read_from_proc(self):
        if not self.proc or not self.proc.stdout:
            return None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return None
            line_str = line.strip()
            if not line_str:
                continue
            try:
                return json.loads(line_str)
            except Exception:
                pass

    def init_handshake(self):
        self.send_to_proc({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "clientInfo": {"name": "antigravity_bridge", "version": "1.1.0"}
            }
        })
        self.init_res = self.read_from_proc()
        self.send_to_proc({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_proc_tool(self, name, args, call_id):
        for _ in range(5):
            self.send_to_proc({
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": args}
            })
            res = self.read_from_proc()
            if res:
                if res.get("id") == call_id:
                    if "result" in res:
                        content = res["result"].get("content", [])
                        is_error = res["result"].get("isError", False)
                        if is_error and content and isinstance(content, list) and len(content) > 0:
                            err_text = content[0].get("text", "")
                            if "Not connected" in err_text:
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
            res = self.call_proc_tool("list_roblox_studios", {}, 900)
            if res and "result" in res:
                content = res["result"].get("content", [])
                if content and isinstance(content, list) and len(content) > 0:
                    txt = content[0].get("text", "{}")
                    data = json.loads(txt)
                    studios = data.get("studios", [])
                    if studios and isinstance(studios, list) and len(studios) > 0:
                        sid = studios[0].get("id")
                        if sid:
                            set_res = self.call_proc_tool("set_active_studio", {"studio_id": sid}, 901)
                            if set_res and not set_res.get("result", {}).get("isError", False):
                                self.active_studio_set = True
                                return True
        except Exception:
            pass
        return False

    def handle_message(self, req):
        if not isinstance(req, dict):
            return None

        is_notification = ("id" not in req) or (req.get("id") is None)
        req_id = req.get("id")
        method = req.get("method")

        if method == "initialize":
            init_result = {}
            if self.init_res and isinstance(self.init_res, dict):
                init_result = self.init_res.get("result", {})
            if not init_result:
                init_result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "RobloxStudio", "version": "1.1.0"},
                    "instructions": "Studio MCP Bridge"
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": init_result
            }

        if method == "notifications/initialized":
            return None

        if method == "server/discover":
            self.send_to_proc({
                "jsonrpc": "2.0",
                "id": "list_discover",
                "method": "tools/list",
                "params": {}
            })
            list_res = self.read_from_proc()
            tools_list = list_res.get("result", {}).get("tools", []) if (list_res and isinstance(list_res, dict)) else []
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": tools_list,
                    "serverInfo": {"name": "RobloxStudio", "version": "1.1.0"}
                }
            }

        if method == "tools/list":
            if is_notification:
                self.send_to_proc(req)
                return None
            self.send_to_proc(req)
            resp = self.read_from_proc()
            if resp is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "Subprocess communication error"}
                }
            return resp

        if method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name") if isinstance(params, dict) else None
            # Only auto-bind studio if calling actions other than list/set
            if tool_name not in ["list_roblox_studios", "set_active_studio"]:
                self.ensure_active_studio()
            if is_notification:
                self.send_to_proc(req)
                return None
            self.send_to_proc(req)
            resp = self.read_from_proc()
            if resp is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": "Subprocess communication error"}
                }
            return resp

        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        # For all other methods
        if is_notification:
            self.send_to_proc(req)
            return None

        self.send_to_proc(req)
        resp = self.read_from_proc()
        if resp is None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": "Subprocess communication error"}
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

                req = json.loads(line_str)
                resp = self.handle_message(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()

            except Exception:
                pass

        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass


def main():
    reconfigure_io()
    bridge = StudioMCPBridge()
    bridge.start_process()
    bridge.init_handshake()
    bridge.run()


if __name__ == "__main__":
    main()
