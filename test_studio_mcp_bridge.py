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
    log_debug,
)


class TestStudioMCPBridge(unittest.TestCase):

    def test_find_studio_mcp_path_env_var(self):
        with patch.dict(os.environ, {"STUDIO_MCP_PATH": "/custom/path/StudioMCP.exe"}):
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

            with patch("os.path.expandvars", return_value=tmpdir):
                with patch.dict(os.environ, {}, clear=True):
                    found = find_studio_mcp_path()
                    self.assertEqual(os.path.abspath(found), os.path.abspath(exe2))

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
             patch.object(bridge, "read_from_proc", return_value={"jsonrpc": "2.0", "id": "__bridge_discover__", "result": {"tools": [{"name": "test_tool"}]}}):
            req = {"jsonrpc": "2.0", "id": 2, "method": "server/discover"}
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 2)
            self.assertEqual(len(resp["result"]["tools"]), 1)
            self.assertEqual(resp["result"]["tools"][0]["name"], "test_tool")

    def test_handle_message_server_discover_error(self):
        bridge = StudioMCPBridge()
        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", return_value={"jsonrpc": "2.0", "id": "__bridge_discover__", "error": {"code": -32000, "message": "fail"}}):
            req = {"jsonrpc": "2.0", "id": 2, "method": "server/discover"}
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 2)
            self.assertIn("error", resp)
            self.assertEqual(resp["error"]["code"], -32000)

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

    def test_ensure_active_studio_multiple_studios_fallback(self):
        bridge = StudioMCPBridge()

        mock_responses = [
            # Response to list_roblox_studios returning 2 studios
            {
                "jsonrpc": "2.0",
                "id": "__bridge_tool_list_0",
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"studios": [{"id": "dead_studio"}, {"id": "alive_studio"}]})}]
                }
            },
            # First set_active_studio fails (isError: True)
            {
                "jsonrpc": "2.0",
                "id": "__bridge_tool_set_0",
                "result": {
                    "content": [{"type": "text", "text": "Failed to connect to dead_studio"}],
                    "isError": True
                }
            },
            # Second set_active_studio succeeds
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

    def test_tool_call_not_connected_resets_and_retries(self):
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
        success_resp = {
            "jsonrpc": "2.0",
            "id": 5,
            "result": {
                "content": [{"type": "text", "text": "Command executed successfully"}]
            }
        }

        with patch.object(bridge, "ensure_active_studio", side_effect=[True, True]) as mock_ensure, \
             patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", side_effect=[not_connected_resp, success_resp]):
            req = {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "execute_luau", "arguments": {}}
            }
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 5)
            self.assertIn("Command executed successfully", resp["result"]["content"][0]["text"])
            self.assertEqual(mock_ensure.call_count, 2)

    def test_subprocess_disconnect_returns_jsonrpc_error(self):
        bridge = StudioMCPBridge()
        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", return_value=None):
            req = {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 10)
            self.assertIn("error", resp)
            self.assertEqual(resp["error"]["code"], -32603)

    def test_handle_invalid_request_type(self):
        bridge = StudioMCPBridge()
        resp = bridge.handle_message("not a dict")
        self.assertEqual(resp["error"]["code"], -32600)

    def test_ping_method(self):
        bridge = StudioMCPBridge()
        resp = bridge.handle_message({"jsonrpc": "2.0", "id": 42, "method": "ping"})
        self.assertEqual(resp["id"], 42)
        self.assertEqual(resp["result"], {})

    def test_start_process_not_found_raises(self):
        bridge = StudioMCPBridge()
        with patch("studio_mcp_bridge.find_studio_mcp_path", return_value=None):
            with self.assertRaises(FileNotFoundError):
                bridge.start_process()

    def test_read_from_proc_id_matching(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        # Simulates subprocess sending an unrelated notification then the expected response
        lines = [
            json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": "wrong_id", "result": {}}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": "target_id", "result": {"ok": True}}) + "\n",
        ]
        mock_proc.stdout.readline.side_effect = lines
        bridge = StudioMCPBridge(proc=mock_proc)

        msg = bridge.read_from_proc(expected_id="target_id")
        self.assertIsNotNone(msg)
        self.assertEqual(msg["id"], "target_id")
        self.assertTrue(msg["result"]["ok"])

    def test_debug_logging_env_var(self):
        with patch.dict(os.environ, {"STUDIO_MCP_DEBUG": "1"}):
            self.assertTrue(is_debug_enabled())
            with patch("sys.stderr.write") as mock_stderr:
                log_debug("test message")
                mock_stderr.assert_called_with("[StudioMCP-Bridge] test message\n")

        with patch.dict(os.environ, {"STUDIO_MCP_DEBUG": "false"}):
            self.assertFalse(is_debug_enabled())

    def test_run_loop_handles_exception_and_parse_error(self):
        bridge = StudioMCPBridge()
        # Feed one invalid JSON line, one valid request that raises in handle_message, then EOF
        inputs = [
            "invalid json here\n",
            json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/list"}) + "\n",
            ""
        ]
        with patch("sys.stdin.readline", side_effect=inputs), \
             patch.object(bridge, "handle_message", side_effect=RuntimeError("something crashed")), \
             patch("sys.stdout.write") as mock_stdout:
            bridge.run()

            outputs = [json.loads(call[0][0]) for call in mock_stdout.call_args_list if call[0][0].strip()]
            self.assertEqual(outputs[0]["error"]["code"], -32700) # parse error
            self.assertEqual(outputs[1]["id"], 99)
            self.assertEqual(outputs[1]["error"]["code"], -32603) # internal bridge error

    def test_close_teardown(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        bridge = StudioMCPBridge(proc=mock_proc)
        bridge.close()
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=2)


if __name__ == "__main__":
    unittest.main()
