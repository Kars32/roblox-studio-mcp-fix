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

    def test_extract_text_from_content(self):
        from studio_mcp_bridge import extract_text_from_content
        self.assertEqual(extract_text_from_content("plain text"), "plain text")
        self.assertEqual(extract_text_from_content([{"type": "text", "text": "hello"}]), "hello")
        self.assertEqual(extract_text_from_content({"text": "dict text"}), "dict text")
        self.assertEqual(extract_text_from_content([]), "")
        self.assertEqual(extract_text_from_content(None), "")

    def test_find_studio_mcp_path_quoted_env_and_case_insensitive(self):
        with patch.dict(os.environ, {"STUDIO_MCP_PATH": '"/custom/path/StudioMCP.exe"'}):
            with patch("os.path.isfile", return_value=True):
                path = find_studio_mcp_path()
                self.assertEqual(path, "/custom/path/StudioMCP.exe")

        with tempfile.TemporaryDirectory() as tmpdir:
            v1 = os.path.join(tmpdir, "version-lower")
            os.makedirs(v1)
            exe_lower = os.path.join(v1, "studiomcp.exe")
            with open(exe_lower, "w") as f:
                f.write("exe")

            with patch("os.path.expandvars", return_value=tmpdir):
                with patch.dict(os.environ, {}, clear=True):
                    found = find_studio_mcp_path()
                    self.assertEqual(os.path.abspath(found), os.path.abspath(exe_lower))

    def test_read_response_notification_forwarding(self):
        mock_proc = MagicMock()
        mock_stdout = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}) + "\n" +
            json.dumps({"jsonrpc": "2.0", "id": "req_1", "result": {}}) + "\n"
        )
        mock_proc.stdout = mock_stdout
        mock_proc.poll.return_value = None

        bridge = StudioMCPBridge(proc=mock_proc)
        with patch("sys.stdout", new_callable=io.StringIO) as fake_stdout:
            resp = bridge.read_response(expected_id="req_1")
            self.assertEqual(resp["id"], "req_1")
            forwarded = fake_stdout.getvalue()
            self.assertIn("notifications/tools/list_changed", forwarded)

    def test_ensure_proc_running_restarts_dead_process(self):
        mock_dead_proc = MagicMock()
        mock_dead_proc.poll.return_value = 1

        bridge = StudioMCPBridge(proc=mock_dead_proc)
        with patch.object(bridge, "start_process") as mock_start, \
             patch.object(bridge, "init_proc") as mock_init:
            bridge.ensure_proc_running()
            mock_start.assert_called_once()
            mock_init.assert_called_once()

    def test_handle_message_tools_call_auto_retry(self):
        bridge = StudioMCPBridge()
        bridge.active_studio_set = True

        not_connected_resp = {
            "jsonrpc": "2.0",
            "id": 99,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "Not connected to Studio"}]
            }
        }
        success_retry_resp = {
            "jsonrpc": "2.0",
            "id": 99,
            "result": {
                "isError": False,
                "content": [{"type": "text", "text": "Executed successfully"}]
            }
        }

        read_responses = [not_connected_resp, success_retry_resp]

        with patch.object(bridge, "ensure_active_studio", return_value=True) as mock_ensure, \
             patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", side_effect=read_responses):

            req = {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {"name": "execute_luau", "arguments": {}}
            }
            resp = bridge.handle_message(req)
            self.assertEqual(resp["result"]["content"][0]["text"], "Executed successfully")
            mock_ensure.assert_called()


if __name__ == "__main__":
    unittest.main()
