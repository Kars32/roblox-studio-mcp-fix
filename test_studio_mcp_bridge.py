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
    is_debug,
    debug_log
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

    def test_is_debug(self):
        with patch.dict(os.environ, {"STUDIO_MCP_DEBUG": "1"}):
            self.assertTrue(is_debug())
        with patch.dict(os.environ, {"STUDIO_MCP_DEBUG": "true"}):
            self.assertTrue(is_debug())
        with patch.dict(os.environ, {"STUDIO_MCP_DEBUG": "0"}):
            self.assertFalse(is_debug())
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_debug())

    def test_debug_log_writes_to_stderr(self):
        with patch.dict(os.environ, {"STUDIO_MCP_DEBUG": "1"}):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                debug_log("test message")
                self.assertIn("[STUDIO_MCP_DEBUG] test message\n", mock_stderr.getvalue())

    def test_init_proc(self):
        mock_proc = MagicMock()
        mock_proc.stdin = io.StringIO()
        mock_proc.poll.return_value = None

        bridge = StudioMCPBridge(proc=mock_proc)
        with patch.object(bridge, "read_matching_from_proc", return_value={"jsonrpc": "2.0", "id": "__bridge_init__", "result": {"protocolVersion": "2024-11-05"}}):
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
             patch.object(bridge, "read_matching_from_proc", return_value={"jsonrpc": "2.0", "id": "list_discover", "result": {"tools": [{"name": "test_tool"}]}}):
            req = {"jsonrpc": "2.0", "id": 2, "method": "server/discover"}
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 2)
            self.assertEqual(len(resp["result"]["tools"]), 1)
            self.assertEqual(resp["result"]["tools"][0]["name"], "test_tool")

    def test_handle_message_server_discover_proc_none(self):
        bridge = StudioMCPBridge()
        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_matching_from_proc", return_value=None):
            req = {"jsonrpc": "2.0", "id": 2, "method": "server/discover"}
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 2)
            self.assertIn("error", resp)
            self.assertEqual(resp["error"]["code"], -32603)

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

    def test_read_from_proc_forwards_notifications(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        # Subprocess emits a notification line followed by a response line
        notification_line = json.dumps({"jsonrpc": "2.0", "method": "server/event", "params": {"ev": 1}}) + "\n"
        response_line = json.dumps({"jsonrpc": "2.0", "id": "req_1", "result": {"status": "ok"}}) + "\n"
        mock_proc.stdout.readline.side_effect = [notification_line, response_line]

        bridge = StudioMCPBridge(proc=mock_proc)
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            msg = bridge.read_from_proc()
            self.assertEqual(msg, {"jsonrpc": "2.0", "id": "req_1", "result": {"status": "ok"}})
            # Verify notification was forwarded to stdout
            stdout_val = mock_stdout.getvalue()
            self.assertIn('"method": "server/event"', stdout_val)

    def test_read_matching_from_proc_ignores_mismatched(self):
        bridge = StudioMCPBridge()
        msg_mismatch = {"jsonrpc": "2.0", "id": "wrong_id", "result": {}}
        msg_match = {"jsonrpc": "2.0", "id": "expected_id", "result": {"val": 42}}

        with patch.object(bridge, "read_from_proc", side_effect=[msg_mismatch, msg_match]):
            result = bridge.read_matching_from_proc("expected_id")
            self.assertEqual(result, msg_match)

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
             patch.object(bridge, "read_matching_from_proc", side_effect=mock_responses):
            success = bridge.ensure_active_studio()
            self.assertTrue(success)
            self.assertTrue(bridge.active_studio_set)

    def test_ensure_active_studio_error_response(self):
        bridge = StudioMCPBridge()
        error_resp = {
            "jsonrpc": "2.0",
            "id": "__bridge_tool_list_0",
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "Studio error"}]
            }
        }
        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_matching_from_proc", return_value=error_resp):
            success = bridge.ensure_active_studio()
            self.assertFalse(success)
            self.assertFalse(bridge.active_studio_set)

    def test_ensure_active_studio_empty_content(self):
        bridge = StudioMCPBridge()
        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_matching_from_proc", return_value={"jsonrpc": "2.0", "id": "__bridge_tool_list_0", "result": {"content": []}}):
            success = bridge.ensure_active_studio()
            self.assertFalse(success)
            self.assertFalse(bridge.active_studio_set)

    def test_tool_call_not_connected_rebinds_and_retries(self):
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
        recovered_resp = {
            "jsonrpc": "2.0",
            "id": 5,
            "result": {
                "isError": False,
                "content": [{"type": "text", "text": "Command succeeded"}]
            }
        }

        with patch.object(bridge, "ensure_active_studio", side_effect=[True, True]) as mock_ensure, \
             patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_matching_from_proc", side_effect=[not_connected_resp, recovered_resp]):
            req = {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "execute_luau", "arguments": {}}
            }
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 5)
            self.assertEqual(resp["result"]["content"][0]["text"], "Command succeeded")
            self.assertEqual(mock_ensure.call_count, 2)

    def test_subprocess_disconnect_returns_jsonrpc_error(self):
        bridge = StudioMCPBridge()
        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_matching_from_proc", return_value=None):
            req = {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 10)
            self.assertIn("error", resp)
            self.assertEqual(resp["error"]["code"], -32603)

    def test_run_batch_jsonrpc_requests(self):
        bridge = StudioMCPBridge()
        batch_input = json.dumps([
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"}
        ]) + "\n"

        with patch("sys.stdin", io.StringIO(batch_input)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            bridge.run()
            output = mock_stdout.getvalue().strip()
            resps = json.loads(output)
            self.assertIsInstance(resps, list)
            self.assertEqual(len(resps), 2)
            self.assertEqual(resps[0]["id"], 1)
            self.assertEqual(resps[1]["id"], 2)

    def test_close_teardown(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin = MagicMock()
        bridge = StudioMCPBridge(proc=mock_proc)
        bridge.close()
        mock_proc.stdin.close.assert_called_once()
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=2)


if __name__ == "__main__":
    unittest.main()
