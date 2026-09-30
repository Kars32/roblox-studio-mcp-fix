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

            with patch("os.environ.get", side_effect=lambda k: tmpdir if k == "LOCALAPPDATA" else None):
                found = find_studio_mcp_path()
                self.assertIsNotNone(found)
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
             patch.object(bridge, "read_from_proc", return_value={"jsonrpc": "2.0", "id": "__discover_2", "result": {"tools": [{"name": "test_tool"}]}}):
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

    def test_client_set_active_studio_updates_state(self):
        bridge = StudioMCPBridge()
        bridge.active_studio_set = False

        set_success_resp = {
            "jsonrpc": "2.0",
            "id": 12,
            "result": {
                "isError": False,
                "content": [{"type": "text", "text": "Studio set"}]
            }
        }

        with patch.object(bridge, "send_to_proc", return_value=True), \
             patch.object(bridge, "read_from_proc", return_value=set_success_resp):
            req = {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {"name": "set_active_studio", "arguments": {"studio_id": "studio_999"}}
            }
            resp = bridge.handle_message(req)
            self.assertEqual(resp["id"], 12)
            self.assertTrue(bridge.active_studio_set)

    def test_response_id_matching_and_buffering(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        msg1 = json.dumps({"jsonrpc": "2.0", "id": "other_id", "result": {"data": "other"}}) + "\n"
        msg2 = json.dumps({"jsonrpc": "2.0", "id": "target_id", "result": {"data": "target"}}) + "\n"

        mock_proc.stdout = io.StringIO(msg1 + msg2)

        bridge = StudioMCPBridge(proc=mock_proc)

        res = bridge.read_from_proc(expected_id="target_id")
        self.assertEqual(res["id"], "target_id")
        self.assertEqual(res["result"]["data"], "target")

        # The other_id response should now be buffered in pending_responses
        self.assertIn("other_id", bridge.pending_responses)
        buffered_res = bridge.read_from_proc(expected_id="other_id")
        self.assertEqual(buffered_res["id"], "other_id")
        self.assertEqual(buffered_res["result"]["data"], "other")

    def test_notification_forwarding_during_read(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        notify_line = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 50}}) + "\n"
        resp_line = json.dumps({"jsonrpc": "2.0", "id": "target_id", "result": {"ok": True}}) + "\n"

        mock_proc.stdout = io.StringIO(notify_line + resp_line)

        bridge = StudioMCPBridge(proc=mock_proc)

        fake_stdout = io.StringIO()
        with patch("sys.stdout", fake_stdout):
            res = bridge.read_from_proc(expected_id="target_id")

        self.assertEqual(res["id"], "target_id")
        self.assertIn("notifications/progress", fake_stdout.getvalue())

    def test_auto_recovery_when_process_terminated(self):
        mock_proc_old = MagicMock()
        mock_proc_old.poll.return_value = 1  # Process terminated

        mock_proc_new = MagicMock()
        mock_proc_new.poll.return_value = None  # New process running

        bridge = StudioMCPBridge(proc=mock_proc_old)

        def side_effect_start():
            bridge.proc = mock_proc_new

        with patch.object(bridge, "start_process", side_effect=side_effect_start) as mock_start, \
             patch.object(bridge, "init_proc") as mock_init:
            bridge.ensure_process_running()
            mock_start.assert_called_once()
            mock_init.assert_called_once()

    def test_ensure_process_running_no_infinite_recursion(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Always terminated

        bridge = StudioMCPBridge(proc=mock_proc)

        with patch.object(bridge, "start_process") as mock_start:
            bridge.ensure_process_running()
            mock_start.assert_called_once()
            # Verify send_to_proc returns False without recursion error
            sent = bridge.send_to_proc({"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertFalse(sent)

    def test_run_returns_error_on_unhandled_exception(self):
        bridge = StudioMCPBridge()

        in_str = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {}}) + "\n"
        sys_stdin = io.StringIO(in_str)
        sys_stdout = io.StringIO()

        with patch("sys.stdin", sys_stdin), \
             patch("sys.stdout", sys_stdout), \
             patch.object(bridge, "handle_message", side_effect=RuntimeError("Unexpected error")):
            bridge.run()

        output = sys_stdout.getvalue()
        self.assertIn('"id": 99', output)
        self.assertIn("Internal bridge error: Unexpected error", output)
        self.assertIn('"code": -32603', output)

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
