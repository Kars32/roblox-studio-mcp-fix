import os
import sys
import io
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import studio_mcp_bridge
from studio_mcp_bridge import StudioMCPBridge, find_studio_mcp_path, reconfigure_io


class TestStudioMCPBridge(unittest.TestCase):

    def test_find_studio_mcp_path_env_var(self):
        with patch.dict(os.environ, {"STUDIO_MCP_PATH": "/custom/path/StudioMCP.exe"}):
            with patch("os.path.isfile", return_value=True):
                path = find_studio_mcp_path()
                self.assertEqual(path, "/custom/path/StudioMCP.exe")

    def test_find_studio_mcp_path_mtime_sorting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            versions = os.path.join(tmpdir, "Roblox", "Versions")
            v1 = os.path.join(versions, "version-1")
            v2 = os.path.join(versions, "version-2")
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

            with patch.dict(os.environ, {"LOCALAPPDATA": tmpdir}, clear=True):
                found = find_studio_mcp_path()
                self.assertIsNotNone(found)
                self.assertEqual(os.path.abspath(found), os.path.abspath(exe2))

    def test_find_studio_mcp_path_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            v1 = os.path.join(tmpdir, "Roblox", "Versions", "version-lowercase")
            os.makedirs(v1)
            exe_lower = os.path.join(v1, "studiomcp.exe")
            with open(exe_lower, "w") as f:
                f.write("lower")

            with patch.dict(os.environ, {"LOCALAPPDATA": tmpdir}, clear=True):
                found = find_studio_mcp_path()
                self.assertIsNotNone(found)
                self.assertEqual(os.path.abspath(found), os.path.abspath(exe_lower))

    def test_response_id_correlation_and_out_of_order(self):
        bridge = StudioMCPBridge()
        responses = [
            {"jsonrpc": "2.0", "id": "other_id", "result": "other"},
            {"jsonrpc": "2.0", "id": "target_id", "result": "target"},
        ]
        with patch.object(bridge, "read_from_proc", side_effect=responses):
            res = bridge.read_response_for_id("target_id")
            self.assertEqual(res["result"], "target")
            self.assertIn("other_id", bridge.pending_responses)

            cached_other = bridge.read_response_for_id("other_id")
            self.assertEqual(cached_other["result"], "other")

    def test_notification_forwarded_during_response_wait(self):
        bridge = StudioMCPBridge()
        responses = [
            {"jsonrpc": "2.0", "method": "notifications/tools_changed", "params": {}},
            {"jsonrpc": "2.0", "id": "req_1", "result": "ok"},
        ]
        out_capture = io.StringIO()
        with patch.object(bridge, "read_from_proc", side_effect=responses), \
             patch("sys.stdout", out_capture):
            res = bridge.read_response_for_id("req_1")
            self.assertEqual(res["result"], "ok")
            written = out_capture.getvalue()
            self.assertIn("notifications/tools_changed", written)

    def test_subprocess_auto_reconnect(self):
        mock_proc_dead = MagicMock()
        mock_proc_dead.poll.return_value = 1

        bridge = StudioMCPBridge(proc=mock_proc_dead)
        with patch.object(bridge, "start_process") as mock_start, \
             patch.object(bridge, "init_proc") as mock_init:
            # Simulate start_process creating a healthy process
            def side_effect_start():
                bridge.proc = MagicMock()
                bridge.proc.poll.return_value = None

            mock_start.side_effect = side_effect_start
            running = bridge.ensure_process_running()
            self.assertTrue(running)
            mock_start.assert_called_once()
            mock_init.assert_called_once()

    def test_run_loop_handles_eof_and_closed_stdin(self):
        bridge = StudioMCPBridge()
        with patch("sys.stdin.readline", side_effect=["", OSError("Closed")]):
            # Should exit cleanly without raising unhandled exception
            bridge.run()

    def test_is_studio_disconnected_error_detection(self):
        err_response_1 = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32000, "message": "No active studio session bound"}
        }
        err_response_2 = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "isError": True,
                "content": [
                    {"type": "text", "text": "Some preamble"},
                    {"type": "text", "text": "Not connected to Roblox Studio"}
                ]
            }
        }
        self.assertTrue(studio_mcp_bridge._is_studio_disconnected_error(err_response_1))
        self.assertTrue(studio_mcp_bridge._is_studio_disconnected_error(err_response_2))

    def test_init_proc(self):
        mock_proc = MagicMock()
        mock_proc.stdin = io.StringIO()
        mock_proc.poll.return_value = None

        bridge = StudioMCPBridge(proc=mock_proc)
        with patch.object(bridge, "read_response_for_id", return_value={"jsonrpc": "2.0", "id": "__bridge_init__", "result": {"protocolVersion": "2024-11-05"}}):
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
             patch.object(bridge, "read_from_proc", return_value={"jsonrpc": "2.0", "id": "list_discover", "result": {"tools": [{"name": "test_tool"}]}}):
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

    def test_subprocess_disconnect_returns_jsonrpc_error(self):
        bridge = StudioMCPBridge()
        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", return_value=None):
            req = {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 10)
            self.assertIn("error", resp)
            self.assertEqual(resp["error"]["code"], -32603)

    def test_close_teardown(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        bridge = StudioMCPBridge(proc=mock_proc)
        bridge.close()
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=2)


if __name__ == "__main__":
    unittest.main()
