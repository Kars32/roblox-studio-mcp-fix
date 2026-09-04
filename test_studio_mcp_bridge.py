import unittest
import os
import sys
import json
import tempfile
import time
from unittest.mock import MagicMock, patch

import studio_mcp_bridge

class TestStudioMCPBridge(unittest.TestCase):

    def test_find_studio_mcp_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertIsNone(studio_mcp_bridge.find_studio_mcp_path(tmpdir))

            v1 = os.path.join(tmpdir, "version-1")
            v2 = os.path.join(tmpdir, "version-2")
            os.makedirs(v1)
            os.makedirs(v2)

            exe1 = os.path.join(v1, "StudioMCP.exe")
            exe2 = os.path.join(v2, "StudioMCP.exe")

            with open(exe1, "w") as f:
                f.write("mock1")
            time.sleep(0.05)
            with open(exe2, "w") as f:
                f.write("mock2")

            found = studio_mcp_bridge.find_studio_mcp_path(tmpdir)
            self.assertEqual(found, exe2)

    def test_check_and_handle_disconnection(self):
        studio_mcp_bridge.active_studio_set = True

        resp_connected = {
            "jsonrpc": "2.0",
            "result": {
                "content": [{"type": "text", "text": "Success"}]
            }
        }
        studio_mcp_bridge.check_and_handle_disconnection(resp_connected)
        self.assertTrue(studio_mcp_bridge.active_studio_set)

        resp_disconnected = {
            "jsonrpc": "2.0",
            "result": {
                "content": [{"type": "text", "text": "Not connected to Studio"}]
            }
        }
        studio_mcp_bridge.check_and_handle_disconnection(resp_disconnected)
        self.assertFalse(studio_mcp_bridge.active_studio_set)

    @patch("studio_mcp_bridge.read_from_proc")
    @patch("studio_mcp_bridge.send_to_proc")
    def test_ensure_active_studio(self, mock_send, mock_read):
        studio_mcp_bridge.active_studio_set = False

        mock_read.side_effect = [
            # Response to list_roblox_studios
            {
                "jsonrpc": "2.0",
                "id": 900,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"studios": [{"id": "studio_123"}]})}]
                }
            },
            # Response to set_active_studio
            {
                "jsonrpc": "2.0",
                "id": 901,
                "result": {
                    "content": [{"type": "text", "text": "Bound studio_123"}],
                    "isError": False
                }
            }
        ]

        res = studio_mcp_bridge.ensure_active_studio()
        self.assertTrue(res)
        self.assertTrue(studio_mcp_bridge.active_studio_set)

    @patch("studio_mcp_bridge.read_from_proc")
    @patch("studio_mcp_bridge.send_to_proc")
    def test_call_proc_tool_error(self, mock_send, mock_read):
        mock_read.return_value = {
            "jsonrpc": "2.0",
            "id": 10,
            "error": {"code": -32601, "message": "Method not found"}
        }

        res = studio_mcp_bridge.call_proc_tool("unknown_tool", {}, 10)
        self.assertIsNotNone(res)
        self.assertIn("error", res)

if __name__ == "__main__":
    unittest.main()
