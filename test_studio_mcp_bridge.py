import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock, mock_open
import tempfile
import shutil

import studio_mcp_bridge


class TestStudioMCPBridge(unittest.TestCase):

    def test_find_studio_mcp_finds_newest_executable(self):
        temp_dir = tempfile.mkdtemp()
        try:
            ver1 = os.path.join(temp_dir, "version-1")
            ver2 = os.path.join(temp_dir, "version-2")
            os.makedirs(ver1)
            os.makedirs(ver2)

            exe1 = os.path.join(ver1, "StudioMCP.exe")
            exe2 = os.path.join(ver2, "StudioMCP.exe")

            with open(exe1, "w") as f:
                f.write("old")
            with open(exe2, "w") as f:
                f.write("new")

            # Set mtime explicitly
            os.utime(exe1, (1000, 1000))
            os.utime(exe2, (2000, 2000))

            with patch("os.path.expandvars", return_value=temp_dir):
                found_path = studio_mcp_bridge.find_studio_mcp()
                self.assertEqual(os.path.abspath(found_path), os.path.abspath(exe2))
        finally:
            shutil.rmtree(temp_dir)

    @patch("subprocess.Popen")
    @patch("studio_mcp_bridge.find_studio_mcp")
    def test_main_handles_initialize_and_discover(self, mock_find, mock_popen):
        mock_find.return_value = "C:\\Fake\\StudioMCP.exe"

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        # Mock responses from StudioMCP.exe
        # 1st read: response to initialize handshake
        # 2nd read: response to tools/list during server/discover
        # Subsequent reads: None (EOF)
        mock_proc.stdout.readline.side_effect = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": "list_discover", "result": {"tools": [{"name": "test_tool"}]}}) + "\n",
            ""
        ]

        client_requests = [
            json.dumps({"jsonrpc": "2.0", "id": 10, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "id": 11, "method": "server/discover"}),
            ""
        ]

        mock_stdin = MagicMock()
        mock_stdin.readline.side_effect = client_requests

        mock_stdout = MagicMock()

        with patch("sys.stdin", mock_stdin), patch("sys.stdout", mock_stdout):
            studio_mcp_bridge.main()

        # Check stdout outputs written to client
        written_lines = [call.args[0] for call in mock_stdout.write.call_args_list]
        parsed_outputs = [json.loads(line) for line in written_lines if line.strip()]

        self.assertEqual(len(parsed_outputs), 2)
        # Check initialize response
        self.assertEqual(parsed_outputs[0]["id"], 10)
        self.assertEqual(parsed_outputs[0]["result"]["protocolVersion"], "2024-11-05")

        # Check discover response
        self.assertEqual(parsed_outputs[1]["id"], 11)
        self.assertEqual(parsed_outputs[1]["result"]["tools"][0]["name"], "test_tool")

    @patch("subprocess.Popen")
    @patch("studio_mcp_bridge.find_studio_mcp")
    def test_invalid_json_returns_parse_error(self, mock_find, mock_popen):
        mock_find.return_value = "C:\\Fake\\StudioMCP.exe"

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        mock_proc.stdout.readline.side_effect = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n",
            ""
        ]

        client_requests = [
            "invalid json {",
            ""
        ]

        mock_stdin = MagicMock()
        mock_stdin.readline.side_effect = client_requests
        mock_stdout = MagicMock()

        with patch("sys.stdin", mock_stdin), patch("sys.stdout", mock_stdout):
            studio_mcp_bridge.main()

        written_lines = [call.args[0] for call in mock_stdout.write.call_args_list]
        parsed_outputs = [json.loads(line) for line in written_lines if line.strip()]

        self.assertEqual(len(parsed_outputs), 1)
        self.assertEqual(parsed_outputs[0]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
