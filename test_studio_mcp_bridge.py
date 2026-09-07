import unittest
from unittest.mock import MagicMock, patch
import json
import os
import io

import studio_mcp_bridge
from studio_mcp_bridge import StudioMCPBridge, find_studio_mcp_path


class TestStudioMCPBridge(unittest.TestCase):

    @patch.dict(os.environ, {"STUDIO_MCP_PATH": "/custom/path/StudioMCP.exe"})
    @patch("os.path.isfile", return_value=True)
    def test_find_studio_mcp_path_env_var(self, mock_isfile):
        path = find_studio_mcp_path()
        self.assertEqual(path, "/custom/path/StudioMCP.exe")

    @patch("os.path.exists", return_value=True)
    @patch("os.walk")
    def test_find_studio_mcp_path_fallback(self, mock_walk, mock_exists):
        mock_walk.return_value = [
            ("/mock/Roblox/Versions/version-123", [], ["StudioMCP.exe", "RobloxStudio.exe"])
        ]
        path = find_studio_mcp_path()
        self.assertEqual(path, os.path.join("/mock/Roblox/Versions/version-123", "StudioMCP.exe"))

    def test_init_handshake(self):
        mock_proc = MagicMock()
        mock_proc.stdout.readline.side_effect = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}).encode("utf-8") if isinstance(b"", bytes) else json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}) + "\n"
        ]
        bridge = StudioMCPBridge(proc=mock_proc)
        bridge.init_handshake()
        self.assertIsNotNone(bridge.init_res)
        self.assertEqual(bridge.init_res.get("result", {}).get("protocolVersion"), "2024-11-05")

    def test_ensure_active_studio_success(self):
        mock_proc = MagicMock()
        bridge = StudioMCPBridge(proc=mock_proc)

        def mock_read():
            if not hasattr(mock_read, "called"):
                mock_read.called = True
                return {
                    "jsonrpc": "2.0",
                    "id": 900,
                    "result": {
                        "content": [
                            {"text": json.dumps({"studios": [{"id": "studio_123"}]})}
                        ]
                    }
                }
            return {
                "jsonrpc": "2.0",
                "id": 901,
                "result": {"isError": False}
            }

        bridge.read_from_proc = mock_read
        bridge.send_to_proc = MagicMock()

        success = bridge.ensure_active_studio()
        self.assertTrue(success)
        self.assertTrue(bridge.active_studio_set)

    def test_ensure_active_studio_empty_content(self):
        mock_proc = MagicMock()
        bridge = StudioMCPBridge(proc=mock_proc)
        bridge.read_from_proc = MagicMock(return_value={
            "jsonrpc": "2.0",
            "id": 900,
            "result": {"content": []}
        })
        bridge.send_to_proc = MagicMock()

        success = bridge.ensure_active_studio()
        self.assertFalse(success)
        self.assertFalse(bridge.active_studio_set)

    def test_handle_message_initialize(self):
        bridge = StudioMCPBridge()
        bridge.init_res = {"result": {"serverInfo": {"name": "RobloxStudio", "version": "1.1.0"}}}
        req = {"jsonrpc": "2.0", "id": 42, "method": "initialize"}
        resp = bridge.handle_message(req)
        self.assertEqual(resp["id"], 42)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "RobloxStudio")

    def test_handle_message_server_discover(self):
        bridge = StudioMCPBridge()
        bridge.send_to_proc = MagicMock()
        bridge.read_from_proc = MagicMock(return_value={
            "jsonrpc": "2.0",
            "id": "list_discover",
            "result": {"tools": [{"name": "run_command"}]}
        })
        req = {"jsonrpc": "2.0", "id": 100, "method": "server/discover"}
        resp = bridge.handle_message(req)
        self.assertEqual(resp["id"], 100)
        self.assertEqual(resp["result"]["tools"][0]["name"], "run_command")

    def test_handle_message_tools_call(self):
        bridge = StudioMCPBridge()
        bridge.ensure_active_studio = MagicMock()
        bridge.send_to_proc = MagicMock()
        bridge.read_from_proc = MagicMock(return_value={
            "jsonrpc": "2.0",
            "id": 5,
            "result": {"content": [{"type": "text", "text": "Success"}]}
        })

        req = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "run_script", "arguments": {}}
        }
        resp = bridge.handle_message(req)
        bridge.ensure_active_studio.assert_called_once()
        self.assertEqual(resp["id"], 5)
        self.assertEqual(resp["result"]["content"][0]["text"], "Success")

    def test_handle_message_proc_error(self):
        bridge = StudioMCPBridge()
        bridge.send_to_proc = MagicMock()
        bridge.read_from_proc = MagicMock(return_value=None)

        req = {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}
        resp = bridge.handle_message(req)
        self.assertEqual(resp["id"], 7)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32603)

    def test_handle_message_notification(self):
        bridge = StudioMCPBridge()
        bridge.send_to_proc = MagicMock()

        req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        resp = bridge.handle_message(req)
        self.assertIsNone(resp)


if __name__ == "__main__":
    unittest.main()
