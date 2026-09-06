import os
import sys
import io
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import studio_mcp_bridge
from studio_mcp_bridge import find_studio_mcp, StudioMCPBridge


class TestStudioMCPBridge(unittest.TestCase):
    def test_find_studio_mcp_env_var(self):
        with patch.dict(os.environ, {"STUDIO_MCP_PATH": "/custom/path/StudioMCP.exe"}):
            with patch("os.path.exists", return_value=True):
                path = find_studio_mcp()
                self.assertEqual(path, "/custom/path/StudioMCP.exe")

    def test_find_studio_mcp_versions_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            v1 = os.path.join(tmpdir, "version-1")
            v2 = os.path.join(tmpdir, "version-2")
            os.makedirs(v1)
            os.makedirs(v2)
            exe1 = os.path.join(v1, "StudioMCP.exe")
            exe2 = os.path.join(v2, "StudioMCP.exe")

            with open(exe1, "w") as f:
                f.write("v1")
            time_v1 = 1000.0
            os.utime(exe1, (time_v1, time_v1))

            with open(exe2, "w") as f:
                f.write("v2")
            time_v2 = 2000.0
            os.utime(exe2, (time_v2, time_v2))

            with patch("os.path.expandvars", return_value=tmpdir):
                with patch.dict(os.environ, {}, clear=True):
                    found = find_studio_mcp()
                    self.assertEqual(found, exe2)

    def test_notifications_do_not_wait_for_response(self):
        mock_proc = MagicMock()
        mock_proc.stdin = io.StringIO()
        mock_proc.stdout = io.StringIO()

        bridge = StudioMCPBridge(mock_proc)

        # Send notification without ID
        notification = {"jsonrpc": "2.0", "method": "custom/notify", "params": {"data": 123}}
        with patch.object(bridge, "read_from_proc") as mock_read:
            bridge.handle_incoming_request(notification)
            mock_read.assert_not_called()

        written = mock_proc.stdin.getvalue()
        self.assertIn('"method": "custom/notify"', written)

    def test_initialize_request(self):
        mock_proc = MagicMock()
        bridge = StudioMCPBridge(mock_proc)
        bridge.init_res = {
            "jsonrpc": "2.0",
            "id": "__bridge_init__",
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "RobloxStudioServer", "version": "1.0.0"}
            }
        }

        output = io.StringIO()
        with patch("sys.stdout", output):
            bridge.handle_incoming_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

        resp = json.loads(output.getvalue().strip())
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "RobloxStudioServer")

    def test_server_discover(self):
        mock_proc = MagicMock()
        bridge = StudioMCPBridge(mock_proc)

        with patch.object(bridge, "send_to_proc", return_value=True) as mock_send, \
             patch.object(bridge, "read_from_proc", return_value={"jsonrpc": "2.0", "id": "__bridge_discover__", "result": {"tools": [{"name": "test_tool"}]}}):
            output = io.StringIO()
            with patch("sys.stdout", output):
                bridge.handle_incoming_request({"jsonrpc": "2.0", "id": 10, "method": "server/discover"})

            resp = json.loads(output.getvalue().strip())
            self.assertEqual(resp["id"], 10)
            self.assertEqual(len(resp["result"]["tools"]), 1)
            self.assertEqual(resp["result"]["tools"][0]["name"], "test_tool")

    def test_tool_call_not_connected_resets_studio_binding(self):
        mock_proc = MagicMock()
        bridge = StudioMCPBridge(mock_proc)
        bridge.active_studio_set = True

        not_connected_resp = {
            "jsonrpc": "2.0",
            "id": 5,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "Not connected to Studio"}]
            }
        }

        with patch.object(bridge, "ensure_active_studio") as mock_ensure, \
             patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", return_value=not_connected_resp):
            output = io.StringIO()
            with patch("sys.stdout", output):
                bridge.handle_incoming_request({
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "run_script", "arguments": {}}
                })

            self.assertFalse(bridge.active_studio_set)

    def test_process_disconnect_returns_jsonrpc_error(self):
        mock_proc = MagicMock()
        bridge = StudioMCPBridge(mock_proc)

        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", return_value=None):
            output = io.StringIO()
            with patch("sys.stdout", output):
                bridge.handle_incoming_request({
                    "jsonrpc": "2.0",
                    "id": 42,
                    "method": "tools/list"
                })

            resp = json.loads(output.getvalue().strip())
            self.assertEqual(resp["id"], 42)
            self.assertIn("error", resp)
            self.assertEqual(resp["error"]["code"], -32603)


if __name__ == "__main__":
    unittest.main()
