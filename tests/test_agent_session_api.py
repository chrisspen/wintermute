"""Unit tests for Agent Session API endpoints (start/stop/status)."""

import base64
import hashlib
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from wintermute.db import Database
from wintermute.web.app import create_app


def _create_test_user(db: Database, username: str = "testuser", password: str = "testpass") -> str:
    """Create a test user with proper password hash."""
    salt = os.urandom(16)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    password_hash = base64.b64encode(
        hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
        )
    ).decode("ascii")
    user_id = str(uuid.uuid4())
    db.insert_user(
        user_id=user_id,
        username=username,
        password_hash=password_hash,
        salt=salt_b64,
    )
    return user_id


def _mock_subprocess_run(cmd, **kwargs):
    """Mock subprocess.run for SSH/SCP commands."""
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""

    # Check if it's a mktemp command
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
    if "mktemp" in cmd_str:
        result.stdout = "/tmp/agent_test-agent_mock123\n"
    else:
        result.stdout = ""

    return result


class AgentSessionAPITests(unittest.TestCase):
    """Tests for standalone agent session API."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        app = create_app(self.db)
        self.client = TestClient(app)

        # Create a test user and login
        _create_test_user(self.db)
        self.client.post(
            "/login",
            data={"username": "testuser", "password": "testpass"},
            follow_redirects=False,
        )

        # Create a test VM target (required for session start)
        self.vm_target_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=self.vm_target_id,
            name="test-vm",
            host="localhost",
            user="testuser",
            port=22,
        )

        # Create a test agent with VM target
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=self.vm_target_id,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_session_status_no_session(self) -> None:
        """Test session status when no session is running."""
        response = self.client.get(
            f"/api/agents/{self.agent_id}/session-status",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["running"])
        self.assertIsNone(data["session_id"])

    @patch("subprocess.run", side_effect=_mock_subprocess_run)
    @patch("wintermute.runner.start_session")
    def test_start_session(self, mock_start_session, mock_run) -> None:
        """Test starting a standalone session."""
        response = self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("session_id", data)
        self.assertIn("location", data)
        self.assertIsNotNone(data["session_id"])
        self.assertIsNotNone(data["location"])

    @patch("subprocess.run", side_effect=_mock_subprocess_run)
    @patch("wintermute.runner.start_session")
    def test_session_status_after_start(self, mock_start_session, mock_run) -> None:
        """Test session status after starting a session."""
        # Start a session first
        start_response = self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )
        session_id = start_response.json()["session_id"]

        # Check status
        response = self.client.get(
            f"/api/agents/{self.agent_id}/session-status",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["running"])
        self.assertEqual(data["session_id"], session_id)

    @patch("subprocess.run", side_effect=_mock_subprocess_run)
    @patch("wintermute.runner.start_session")
    def test_start_session_already_running(self, mock_start_session, mock_run) -> None:
        """Test that starting a session fails if one is already running."""
        # Start first session
        self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )

        # Try to start another session
        response = self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("already running", response.json()["detail"])

    @patch("subprocess.run", side_effect=_mock_subprocess_run)
    @patch("wintermute.runner.start_session")
    @patch("wintermute.runner.stop_session")
    def test_stop_session(self, mock_stop_session, mock_start_session, mock_run) -> None:
        """Test stopping a running session."""
        # Start a session first
        self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )

        # Stop the session
        response = self.client.post(
            f"/api/agents/{self.agent_id}/session/stop",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

    def test_stop_session_no_running_session(self) -> None:
        """Test stopping fails when no session is running."""
        response = self.client.post(
            f"/api/agents/{self.agent_id}/session/stop",
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("No running session", response.json()["detail"])

    @patch("subprocess.run", side_effect=_mock_subprocess_run)
    @patch("wintermute.runner.start_session")
    @patch("wintermute.runner.stop_session")
    def test_session_status_after_stop(self, mock_stop_session, mock_start_session, mock_run) -> None:
        """Test session status returns not running after stop."""
        # Start and then stop a session
        self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )
        self.client.post(
            f"/api/agents/{self.agent_id}/session/stop",
        )

        # Check status
        response = self.client.get(
            f"/api/agents/{self.agent_id}/session-status",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["running"])

    def test_start_session_agent_not_found(self) -> None:
        """Test starting session for non-existent agent."""
        response = self.client.post(
            "/api/agents/nonexistent-id/start-session",
        )
        self.assertEqual(response.status_code, 404)

    def test_session_status_agent_not_found(self) -> None:
        """Test session status for non-existent agent."""
        response = self.client.get(
            "/api/agents/nonexistent-id/session-status",
        )
        self.assertEqual(response.status_code, 404)

    def test_start_session_no_vm_target(self) -> None:
        """Test starting session for agent without VM target."""
        # Create agent without VM target
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="No VM Agent",
            slug="no-vm-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )

        response = self.client.post(
            f"/api/agents/{agent_id}/start-session",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("no VM target", response.json()["detail"])


class SessionFileSyncTests(unittest.TestCase):
    """Tests for session file sync on stop and pull."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        app = create_app(self.db)
        self.client = TestClient(app)

        # Create a test user and login
        _create_test_user(self.db)
        self.client.post(
            "/login",
            data={"username": "testuser", "password": "testpass"},
            follow_redirects=False,
        )

        # Create a test VM target
        self.vm_target_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=self.vm_target_id,
            name="test-vm",
            host="localhost",
            user="testuser",
            port=22,
        )

        # Create a session file config with definitions
        self.config_id = str(uuid.uuid4())
        self.db.insert_session_file_config(self.config_id, "Test Config", "Test")
        self.def_state_id = str(uuid.uuid4())
        self.def_todo_id = str(uuid.uuid4())
        self.db.insert_session_file_definition(
            definition_id=self.def_state_id,
            config_id=self.config_id,
            filename="STATE.md",
            default_content="# Default State",
            description="State file",
            required=True,
            sync_on_exit=True,
            sort_order=1,
        )
        self.db.insert_session_file_definition(
            definition_id=self.def_todo_id,
            config_id=self.config_id,
            filename="TODO.md",
            default_content="# Default TODO",
            description="Todo file",
            required=False,
            sync_on_exit=True,
            sort_order=2,
        )

        # Create a test agent with session file config
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=self.vm_target_id,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )
        # Set session file config
        self.db.update_agent(self.agent_id, session_file_config_id=self.config_id)
        # Create initial session files (normally done via UI save)
        self.db.upsert_session_file(self.agent_id, self.def_state_id, "# Default State")
        self.db.upsert_session_file(self.agent_id, self.def_todo_id, "# Default TODO")

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def _mock_scp_with_files(self, files_content: dict):
        """Create a mock for subprocess.run that writes files during SCP."""
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""

            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd

            if "mktemp" in cmd_str:
                result.stdout = "/tmp/agent_test-agent_mock123\n"
            elif "scp" in cmd_str and "-r" in cmd:
                # Find the local destination path from the command
                # SCP command format: scp -P port ... -r user@host:remote/. local/
                for i, part in enumerate(cmd):
                    if part.endswith("/") and not "@" in part:
                        local_dir = part.rstrip("/")
                        # Write the mock files to the local temp dir
                        for filename, content in files_content.items():
                            filepath = os.path.join(local_dir, filename)
                            os.makedirs(os.path.dirname(filepath), exist_ok=True)
                            with open(filepath, "w") as f:
                                f.write(content)
                        break
            elif "test -d" in cmd_str:
                result.stdout = "exists\n"
            elif "mkdir -p" in cmd_str:
                pass  # No-op for mkdir

            return result
        return mock_run

    @patch("wintermute.runner.start_session")
    @patch("wintermute.runner.stop_session")
    def test_stop_session_syncs_files_back(self, mock_stop_session, mock_start_session) -> None:
        """Test that stopping a session syncs session files back to Wintermute."""
        # Mock SCP to return updated file content
        mock_files = {
            "STATE.md": "# Updated State\nThis was modified by the agent.",
            "TODO.md": "# Updated TODO\n- Task 1 done\n- Task 2 pending",
        }

        with patch("subprocess.run", side_effect=self._mock_scp_with_files(mock_files)):
            # Start a session
            start_response = self.client.post(
                f"/api/agents/{self.agent_id}/start-session",
            )
            self.assertEqual(start_response.status_code, 200)

            # Verify session files were created with default content initially
            files_before = self.db.list_session_files(self.agent_id)
            self.assertEqual(len(files_before), 2)

            # Stop the session - this should sync files back
            stop_response = self.client.post(
                f"/api/agents/{self.agent_id}/session/stop",
            )
            self.assertEqual(stop_response.status_code, 200)

            # Verify session files were updated with new content
            files_after = self.db.list_session_files(self.agent_id)
            self.assertEqual(len(files_after), 2)

            # Find STATE.md and verify it has updated content
            state_file = None
            todo_file = None
            for f in files_after:
                if f.definition_id == self.def_state_id:
                    state_file = f
                elif f.definition_id == self.def_todo_id:
                    todo_file = f

            self.assertIsNotNone(state_file)
            self.assertIn("Updated State", state_file.content)
            self.assertIn("modified by the agent", state_file.content)

            self.assertIsNotNone(todo_file)
            self.assertIn("Updated TODO", todo_file.content)
            self.assertIn("Task 1 done", todo_file.content)

    @patch("wintermute.runner.start_session")
    def test_pull_session_files_api(self, mock_start_session) -> None:
        """Test the pull-session-files API endpoint."""
        # Set working directory so we don't need an active session
        self.db.update_agent(
            self.agent_id,
            working_directory="/home/testuser/project",
            session_directory=".agent",
        )

        mock_files = {
            "STATE.md": "# Pulled State Content",
            "TODO.md": "# Pulled TODO Content",
        }

        with patch("subprocess.run", side_effect=self._mock_scp_with_files(mock_files)):
            response = self.client.post(
                f"/api/agents/{self.agent_id}/pull-session-files",
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["success"])
            self.assertEqual(len(data["files"]), 2)

            # Verify files were saved
            files = self.db.list_session_files(self.agent_id)
            self.assertEqual(len(files), 2)

            state_file = None
            for f in files:
                if f.definition_id == self.def_state_id:
                    state_file = f
                    break

            self.assertIsNotNone(state_file)
            self.assertIn("Pulled State Content", state_file.content)

    def test_pull_session_files_no_vm_target(self) -> None:
        """Test pull fails when agent has no VM target."""
        # Create agent without VM target
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="No VM Agent",
            slug="no-vm-agent2",
            command="echo test",
            session_mode="tmux",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )

        response = self.client.post(
            f"/api/agents/{agent_id}/pull-session-files",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("no VM target", response.json()["detail"])

    def test_pull_session_files_no_config(self) -> None:
        """Test pull fails when agent has no session file config."""
        # Create agent without session file config
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="No Config Agent",
            slug="no-config-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=self.vm_target_id,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )

        response = self.client.post(
            f"/api/agents/{agent_id}/pull-session-files",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("no session file config", response.json()["detail"])


class SessionFileTimestampConflictTests(unittest.TestCase):
    """Tests for session file timestamp conflict detection on agent start."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        app = create_app(self.db)
        self.client = TestClient(app)

        # Create a test user and login
        _create_test_user(self.db)
        self.client.post(
            "/login",
            data={"username": "testuser", "password": "testpass"},
            follow_redirects=False,
        )

        # Create a test VM target
        self.vm_target_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=self.vm_target_id,
            name="test-vm",
            host="localhost",
            user="testuser",
            port=22,
        )

        # Create a session file config with definitions
        self.config_id = str(uuid.uuid4())
        self.db.insert_session_file_config(self.config_id, "Test Config", "Test")
        self.def_state_id = str(uuid.uuid4())
        self.db.insert_session_file_definition(
            definition_id=self.def_state_id,
            config_id=self.config_id,
            filename="STATE.md",
            default_content="# Default State",
            description="State file",
            required=True,
            sync_on_exit=True,
            sort_order=1,
        )

        # Create a test agent with session file config and working directory
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=self.vm_target_id,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            working_directory="/home/testuser/project",
            session_directory=".agent",
        )
        self.db.update_agent(self.agent_id, session_file_config_id=self.config_id)

        # Create a session file with a known timestamp (old)
        self.db.upsert_session_file(self.agent_id, self.def_state_id, "# Old content")

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def _create_mock_run(self, remote_timestamp: int):
        """Create a mock for subprocess.run that returns specific timestamps."""
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""

            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd

            if "stat -c" in cmd_str:
                # Return the mocked remote timestamp
                result.stdout = f"{remote_timestamp} /home/testuser/project/.agent/STATE.md\n"
            elif "test -d" in cmd_str:
                result.stdout = "exists\n"
            elif "which tmux" in cmd_str or "which echo" in cmd_str:
                result.stdout = "/usr/bin/tmux\n"
            elif "free -b" in cmd_str:
                result.stdout = "Mem: 8000000000 2000000000 6000000000\n"

            return result
        return mock_run

    @patch("wintermute.runner.start_session")
    def test_start_returns_conflict_when_remote_files_newer(self, mock_start_session) -> None:
        """Test that starting returns 409 conflict when remote files are newer."""
        # Get the local file's timestamp
        files = self.db.list_session_files(self.agent_id)
        local_file = files[0]
        from datetime import datetime
        local_dt = datetime.fromisoformat(local_file.updated_at.replace("Z", "+00:00"))
        local_ts = int(local_dt.timestamp())

        # Remote is 1 hour newer
        remote_ts = local_ts + 3600

        with patch("subprocess.run", side_effect=self._create_mock_run(remote_ts)):
            response = self.client.post(
                f"/api/agents/{self.agent_id}/start-session",
            )
            self.assertEqual(response.status_code, 409)
            data = response.json()
            self.assertEqual(data["conflict"], "session_files_newer_on_target")
            self.assertIn("files", data)
            self.assertEqual(len(data["files"]), 1)
            self.assertEqual(data["files"][0]["filename"], "STATE.md")

    @patch("wintermute.runner.start_session")
    def test_start_with_use_target_skips_file_copy(self, mock_start_session) -> None:
        """Test that file_mode=use_target starts without copying Wintermute files."""
        files = self.db.list_session_files(self.agent_id)
        local_file = files[0]
        from datetime import datetime
        local_dt = datetime.fromisoformat(local_file.updated_at.replace("Z", "+00:00"))
        local_ts = int(local_dt.timestamp())
        remote_ts = local_ts + 3600  # Remote is newer

        scp_called = []

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""

            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd

            if "scp" in cmd_str:
                scp_called.append(cmd_str)
            elif "stat -c" in cmd_str:
                result.stdout = f"{remote_ts} /home/testuser/project/.agent/STATE.md\n"
            elif "test -d" in cmd_str:
                result.stdout = "exists\n"
            elif "which tmux" in cmd_str or "which echo" in cmd_str:
                result.stdout = "/usr/bin/tmux\n"
            elif "free -b" in cmd_str:
                result.stdout = "Mem: 8000000000 2000000000 6000000000\n"
            elif "mkdir -p" in cmd_str:
                pass

            return result

        with patch("subprocess.run", side_effect=mock_run):
            response = self.client.post(
                f"/api/agents/{self.agent_id}/start-session",
                json={"file_mode": "use_target"},
            )
            self.assertEqual(response.status_code, 200)
            # SCP should NOT have been called to copy files TO the target
            # (It may be called for other purposes)
            scp_to_target = [c for c in scp_called if "testuser@localhost:" in c and not c.endswith("/")]
            self.assertEqual(len(scp_to_target), 0, "SCP should not copy files to target when using use_target mode")

    @patch("wintermute.runner.start_session")
    def test_start_with_use_wintermute_copies_files(self, mock_start_session) -> None:
        """Test that file_mode=use_wintermute copies Wintermute files even when remote is newer."""
        files = self.db.list_session_files(self.agent_id)
        local_file = files[0]
        from datetime import datetime
        local_dt = datetime.fromisoformat(local_file.updated_at.replace("Z", "+00:00"))
        local_ts = int(local_dt.timestamp())
        remote_ts = local_ts + 3600  # Remote is newer

        scp_called = []

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""

            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd

            if "scp" in cmd_str:
                scp_called.append(cmd_str)
            elif "stat -c" in cmd_str:
                result.stdout = f"{remote_ts} /home/testuser/project/.agent/STATE.md\n"
            elif "test -d" in cmd_str:
                result.stdout = "exists\n"
            elif "which tmux" in cmd_str or "which echo" in cmd_str:
                result.stdout = "/usr/bin/tmux\n"
            elif "free -b" in cmd_str:
                result.stdout = "Mem: 8000000000 2000000000 6000000000\n"
            elif "mkdir -p" in cmd_str:
                pass

            return result

        with patch("subprocess.run", side_effect=mock_run):
            response = self.client.post(
                f"/api/agents/{self.agent_id}/start-session",
                json={"file_mode": "use_wintermute"},
            )
            self.assertEqual(response.status_code, 200)
            # SCP should have been called to copy files to the target
            self.assertTrue(len(scp_called) > 0, "SCP should be called when using use_wintermute mode")

    @patch("wintermute.runner.start_session")
    def test_start_no_conflict_when_local_is_newer(self, mock_start_session) -> None:
        """Test that no conflict is returned when local files are newer than remote."""
        files = self.db.list_session_files(self.agent_id)
        local_file = files[0]
        from datetime import datetime
        local_dt = datetime.fromisoformat(local_file.updated_at.replace("Z", "+00:00"))
        local_ts = int(local_dt.timestamp())

        # Remote is 1 hour older
        remote_ts = local_ts - 3600

        with patch("subprocess.run", side_effect=self._create_mock_run(remote_ts)):
            response = self.client.post(
                f"/api/agents/{self.agent_id}/start-session",
            )
            # Should succeed, not return conflict
            self.assertEqual(response.status_code, 200)
            self.assertIn("session_id", response.json())


if __name__ == "__main__":
    unittest.main()
