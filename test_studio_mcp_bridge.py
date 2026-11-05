import os
import sys
import io
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import studio_mcp_bridge
from studio_mcp_bridge import (
    StudioMCPBridge,
    find_studio_mcp_path,
    reconfigure_io,
    is_debug_enabled,
    log_debug
)


class TestStudioMCPBridge(unittest.TestCase):

    def test_find_studio_mcp_path_env_var(self):
        with patch.dict(os.environ, {"STUDIO_MCP_PATH": "/custom/path/StudioMCP.exe"}):
            with patch("os.path.isfile", return_value=True):
                path = find_studio_mcp_path()
                self.assertEqual(path, "/custom/path/StudioMCP.exe")

    def test_find_studio_mcp_path_env_var_with_quotes(self):
        with patch.dict(os.environ, {"STUDIO_MCP_PATH": '"/custom/path/StudioMCP.exe" '}):
            with patch("os.path.isfile", return_value=True):
                path = find_studio_mcp_path()
                self.assertEqual(path, "/custom/path/StudioMCP.exe")

    def test_find_studio_mcp_path_mtime_sorting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            v1 = os.path.join(tmpdir, "version-1")
            v2 = os.path.join(tmpdir, "version-2")
            os.makedirs(v1)
            os.makedirs(v2)

            exe1 = os.path.join(v1, "StudioMCP.exe")
            exe2 = os.path.join(v2, "StudioMCP.exe")

            with open(exe1, "w") as f:
                f.write("v1")
            with open(exe2, "w") as f:
                f.write("v2")

            os.utime(exe1, (1000.0, 1000.0))
            os.utime(exe2, (2000.0, 2000.0))

            with patch("os.path.expandvars", side_effect=lambda x: tmpdir if "Roblox" in x else x):
                with patch.dict(os.environ, {}, clear=True):
                    found = find_studio_mcp_path()
                    self.assertEqual(os.path.abspath(found), os.path.abspath(exe2))

    def test_debug_logging(self):
        with patch.dict(os.environ, {"STUDIO_MCP_DEBUG": "1"}):
            self.assertTrue(is_debug_enabled())
            with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                log_debug("test message")
                self.assertIn("[StudioMCPBridge] test message", mock_stderr.getvalue())

        with patch.dict(os.environ, {"STUDIO_MCP_DEBUG": "0"}):
            self.assertFalse(is_debug_enabled())

    def test_init_proc(self):
        mock_proc = MagicMock()
        mock_proc.stdin = io.StringIO()
        mock_proc.poll.return_value = None

        bridge = StudioMCPBridge(proc=mock_proc)
        with patch.object(bridge, "read_from_proc", return_value={"jsonrpc": "2.0", "id": "__bridge_init__", "result": {"protocolVersion": "2024-11-05"}}):
            res = bridge.init_proc()
            self.assertIsNotNone(res)
            self.assertEqual(bridge.init_res["result"]["protocolVersion"], "2024-11-05")

        stdin_value = mock_proc.stdin.getvalue()
        self.assertIn('"method": "initialize"', stdin_value)
        self.assertIn('"method": "notifications/initialized"', stdin_value)

    def test_handle_message_initialize(self):
        bridge = StudioMCPBridge()
        bridge.init_res = {"result": {"serverInfo": {"name": "RobloxStudio", "version": "1.2.0"}}}
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        resp = bridge.handle_message(req)
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "RobloxStudio")

    def test_handle_message_server_discover(self):
        bridge = StudioMCPBridge()
        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", return_value={"jsonrpc": "2.0", "id": "list_discover_2", "result": {"tools": [{"name": "test_tool"}]}}):
            req = {"jsonrpc": "2.0", "id": 2, "method": "server/discover"}
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 2)
            self.assertEqual(len(resp["result"]["tools"]), 1)
            self.assertEqual(resp["result"]["tools"][0]["name"], "test_tool")

    def test_handle_message_notification_does_not_wait(self):
        mock_proc = MagicMock()
        mock_proc.stdin = io.StringIO()
        mock_proc.poll.return_value = None

        bridge = StudioMCPBridge(proc=mock_proc)
        with patch.object(bridge, "read_from_proc") as mock_read:
            notification = {"jsonrpc": "2.0", "method": "custom/notify", "params": {"data": 123}}
            resp = bridge.handle_message(notification)
            self.assertIsNone(resp)
            mock_read.assert_not_called()

        written = mock_proc.stdin.getvalue()
        self.assertIn('"method": "custom/notify"', written)

    def test_ensure_active_studio_success(self):
        bridge = StudioMCPBridge()

        mock_responses = [
            # Response to list_roblox_studios
            {
                "jsonrpc": "2.0",
                "id": "__bridge_tool_list_0",
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"studios": [{"id": "studio_123"}]})}]
                }
            },
            # Response to set_active_studio
            {
                "jsonrpc": "2.0",
                "id": "__bridge_tool_set_0",
                "result": {
                    "content": [{"type": "text", "text": "Active studio set"}],
                    "isError": False
                }
            }
        ]

        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", side_effect=mock_responses):
            success = bridge.ensure_active_studio()
            self.assertTrue(success)
            self.assertTrue(bridge.active_studio_set)

    def test_ensure_active_studio_alternate_format(self):
        # Test where list_roblox_studios returns a list directly and uses "studio_id"
        bridge = StudioMCPBridge()

        mock_responses = [
            {
                "jsonrpc": "2.0",
                "id": "__bridge_tool_list_0",
                "result": {
                    "content": [{"type": "text", "text": json.dumps([{"studio_id": "studio_999"}])}]
                }
            },
            {
                "jsonrpc": "2.0",
                "id": "__bridge_tool_set_0",
                "result": {
                    "content": [{"type": "text", "text": "Active studio set"}],
                    "isError": False
                }
            }
        ]

        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", side_effect=mock_responses):
            success = bridge.ensure_active_studio()
            self.assertTrue(success)
            self.assertTrue(bridge.active_studio_set)

    def test_ensure_active_studio_empty_content(self):
        bridge = StudioMCPBridge()
        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", return_value={"jsonrpc": "2.0", "id": "__bridge_tool_list_0", "result": {"content": []}}):
            success = bridge.ensure_active_studio()
            self.assertFalse(success)
            self.assertFalse(bridge.active_studio_set)

    def test_tool_call_not_connected_resets_studio_binding(self):
        bridge = StudioMCPBridge()
        bridge.active_studio_set = True

        not_connected_resp = {
            "jsonrpc": "2.0",
            "id": 5,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "Not connected to Studio"}]
            }
        }

        with patch.object(bridge, "ensure_active_studio"), \
             patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", return_value=not_connected_resp):
            req = {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "execute_luau", "arguments": {}}
            }
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 5)
            self.assertFalse(bridge.active_studio_set)

    def test_tool_call_error_object_resets_studio_binding(self):
        bridge = StudioMCPBridge()
        bridge.active_studio_set = True

        err_resp = {
            "jsonrpc": "2.0",
            "id": 7,
            "error": {"code": -32000, "message": "No active studio found"}
        }

        with patch.object(bridge, "ensure_active_studio"), \
             patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", return_value=err_resp):
            req = {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "execute_luau", "arguments": {}}
            }
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 7)
            self.assertFalse(bridge.active_studio_set)

    def test_subprocess_disconnect_returns_jsonrpc_error(self):
        bridge = StudioMCPBridge()
        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", return_value=None):
            req = {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 10)
            self.assertIn("error", resp)
            self.assertEqual(resp["error"]["code"], -32603)

    def test_read_from_proc_matches_expected_id_and_buffers(self):
        bridge = StudioMCPBridge()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 99, "result": {"other": "value"}}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": 42, "result": {"matched": True}}) + "\n"
        ]
        mock_proc.stdout.readline.side_effect = lines
        bridge.proc = mock_proc

        # Request id 42; 99 should be buffered
        resp = bridge.read_from_proc(expected_id=42)
        self.assertEqual(resp["id"], 42)
        self.assertEqual(resp["result"]["matched"], True)
        self.assertIn(99, bridge._pending_responses)

        # Next read for 99 should retrieve from buffer without reading from stdout
        resp99 = bridge.read_from_proc(expected_id=99)
        self.assertEqual(resp99["id"], 99)
        self.assertNotIn(99, bridge._pending_responses)

    def test_handle_invalid_message_type(self):
        bridge = StudioMCPBridge()
        resp = bridge.handle_message("invalid primitive string")
        self.assertEqual(resp["error"]["code"], -32600)

    def test_run_batch_requests(self):
        bridge = StudioMCPBridge()
        bridge.handle_message = MagicMock(side_effect=lambda r: {"jsonrpc": "2.0", "id": r["id"], "result": {}})

        input_data = json.dumps([
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"}
        ]) + "\n"

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            bridge.run()
            output = mock_stdout.getvalue()
            batch_result = json.loads(output)
            self.assertEqual(len(batch_result), 2)
            self.assertEqual(batch_result[0]["id"], 1)
            self.assertEqual(batch_result[1]["id"], 2)

    def test_close_teardown(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin.closed = False
        mock_proc.stdout.closed = False
        bridge = StudioMCPBridge(proc=mock_proc)
        bridge.close()
        mock_proc.stdin.close.assert_called_once()
        mock_proc.stdout.close.assert_called_once()
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=2)


if __name__ == "__main__":
    unittest.main()
