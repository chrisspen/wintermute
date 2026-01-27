"""Tests for bulk action functionality."""

import base64
import hashlib
import os
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient

from wintermute.db import Database
from wintermute.web.app import create_app, _generate_unique_string


class GenerateUniqueStringTests(unittest.TestCase):
    """Tests for the _generate_unique_string helper function."""

    def test_returns_base_when_not_in_existing(self) -> None:
        """Returns the base string if it doesn't exist."""
        result = _generate_unique_string("bob", {"alice", "charlie"})
        self.assertEqual(result, "bob")

    def test_appends_2_when_base_exists(self) -> None:
        """Appends '2' when base string already exists."""
        result = _generate_unique_string("bob", {"bob", "alice"})
        self.assertEqual(result, "bob2")

    def test_increments_to_next_available(self) -> None:
        """Increments until finding an available number."""
        result = _generate_unique_string("bob", {"bob", "bob2", "bob3"})
        self.assertEqual(result, "bob4")

    def test_handles_existing_numeric_suffix(self) -> None:
        """Handles strings that already have numeric suffixes."""
        result = _generate_unique_string("bob2", {"bob2", "bob3"})
        self.assertEqual(result, "bob4")

    def test_handles_empty_existing_set(self) -> None:
        """Returns base when existing set is empty."""
        result = _generate_unique_string("test", set())
        self.assertEqual(result, "test")

    def test_handles_slug_style_names(self) -> None:
        """Works with slug-style names containing hyphens."""
        result = _generate_unique_string("my-agent", {"my-agent", "my-agent2"})
        self.assertEqual(result, "my-agent3")


class BulkActionCloneTests(unittest.TestCase):
    """Tests for the bulk clone action endpoint."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        self.app = create_app(self.db)
        self.client = TestClient(self.app)
        # Create a test user with properly hashed password
        password = "testpass"
        salt = os.urandom(16)
        salt_b64 = base64.b64encode(salt).decode("ascii")
        password_hash = base64.b64encode(hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
        )).decode("ascii")
        self.db.insert_user(
            user_id=str(uuid.uuid4()),
            username="testuser",
            password_hash=password_hash,
            salt=salt_b64,
        )
        # Login to get session cookie
        self.client.post(
            "/login",
            data={
                "username": "testuser",
                "password": "testpass"
            },
            follow_redirects=False,
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def _create_agent(
        self,
        agent_id: str,
        name: str,
        slug: str,
    ) -> None:
        """Helper to create an agent."""
        self.db.insert_agent(
            agent_id=agent_id,
            name=name,
            slug=slug,
            command="claude",
            session_mode="claude",
            vm_target_id=None,
            required_ssh_options="-o StrictHostKeyChecking=no",
            env_vars="FOO=bar",
            mcp_config=None,
            trust_level="high",
            input_echo_prefix=">>> ",
            response_prefix="RESPONSE:",
            llm_base_url="http://localhost:11434/v1",
            llm_api_key="test-key",
            llm_model="llama3.2",
            average_memory_usage_mb=2000,
        )

    def test_clone_single_agent(self) -> None:
        """Clone action creates a copy of a single agent."""
        self._create_agent("agent-1", "Test Agent", "test-agent")

        response = self.client.post(
            "/ui/agents/bulk-action",
            data={
                "action": "clone",
                "ids": ["agent-1"]
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/ui/", response.headers["location"])

        # Verify the clone was created
        agents = self.db.list_agents()
        self.assertEqual(len(agents), 2)

        # Find the cloned agent
        original = self.db.get_agent("agent-1")
        cloned = [a for a in agents if a.id != "agent-1"][0]

        # Verify unique fields were modified
        self.assertEqual(cloned.name, "Test Agent2")
        self.assertEqual(cloned.slug, "test-agent2")
        self.assertNotEqual(cloned.id, original.id)

        # Verify other fields were copied
        self.assertEqual(cloned.command, original.command)
        self.assertEqual(cloned.session_mode, original.session_mode)
        self.assertEqual(cloned.required_ssh_options, original.required_ssh_options)
        self.assertEqual(cloned.env_vars, original.env_vars)
        self.assertEqual(cloned.trust_level, original.trust_level)
        self.assertEqual(cloned.input_echo_prefix, original.input_echo_prefix)
        self.assertEqual(cloned.response_prefix, original.response_prefix)
        self.assertEqual(cloned.llm_base_url, original.llm_base_url)
        self.assertEqual(cloned.llm_api_key, original.llm_api_key)
        self.assertEqual(cloned.llm_model, original.llm_model)
        self.assertEqual(cloned.average_memory_usage_mb, original.average_memory_usage_mb)

    def test_clone_multiple_agents(self) -> None:
        """Clone action creates copies of multiple selected agents."""
        self._create_agent("agent-1", "Agent Alpha", "agent-alpha")
        self._create_agent("agent-2", "Agent Beta", "agent-beta")

        # Use httpx-compatible format for multiple values
        response = self.client.post(
            "/ui/agents/bulk-action",
            data={
                "action": "clone",
                "ids": ["agent-1", "agent-2"],
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/ui/", response.headers["location"])

        agents = self.db.list_agents()
        self.assertEqual(len(agents), 4)

        names = {a.name for a in agents}
        self.assertIn("Agent Alpha", names)
        self.assertIn("Agent Alpha2", names)
        self.assertIn("Agent Beta", names)
        self.assertIn("Agent Beta2", names)

    def test_clone_handles_existing_numbered_names(self) -> None:
        """Clone increments numbers when numbered versions already exist."""
        self._create_agent("agent-1", "Test Agent", "test-agent")
        self._create_agent("agent-2", "Test Agent2", "test-agent2")

        response = self.client.post(
            "/ui/agents/bulk-action",
            data={
                "action": "clone",
                "ids": ["agent-1"]
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)

        agents = self.db.list_agents()
        self.assertEqual(len(agents), 3)

        names = {a.name for a in agents}
        self.assertIn("Test Agent3", names)

    def test_clone_missing_action_returns_error(self) -> None:
        """Clone with missing action returns 400 error."""
        self._create_agent("agent-1", "Test Agent", "test-agent")

        response = self.client.post(
            "/ui/agents/bulk-action",
            data={"ids": ["agent-1"]},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)

    def test_clone_missing_ids_returns_error(self) -> None:
        """Clone with missing ids returns 400 error."""
        response = self.client.post(
            "/ui/agents/bulk-action",
            data={"action": "clone"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)

    def test_clone_unknown_action_returns_error(self) -> None:
        """Clone with unknown action returns 400 error."""
        self._create_agent("agent-1", "Test Agent", "test-agent")

        response = self.client.post(
            "/ui/agents/bulk-action",
            data={
                "action": "delete",
                "ids": ["agent-1"]
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)

    def test_clone_nonexistent_agent_skipped(self) -> None:
        """Clone skips agents that don't exist."""
        self._create_agent("agent-1", "Test Agent", "test-agent")

        response = self.client.post(
            "/ui/agents/bulk-action",
            data={
                "action": "clone",
                "ids": ["agent-1", "nonexistent-agent"],
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/ui/", response.headers["location"])

        agents = self.db.list_agents()
        self.assertEqual(len(agents), 2)

    def test_agents_page_shows_bulk_actions(self) -> None:
        """Agents list page includes bulk action UI elements."""
        response = self.client.get("/ui/agents")
        self.assertEqual(response.status_code, 200)

        # Check for bulk action elements
        self.assertIn("data-bulk-actions", response.text)
        self.assertIn("data-bulk-model", response.text)
        self.assertIn("data-select-all", response.text)
        self.assertIn("data-bulk-action-select", response.text)
        self.assertIn("data-bulk-action-go", response.text)
        self.assertIn("Clone", response.text)


class SessionFileConfigsBulkActionTests(unittest.TestCase):
    """Tests for the session file configs bulk clone action endpoint."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        self.app = create_app(self.db)
        self.client = TestClient(self.app)
        # Create a test user with properly hashed password
        password = "testpass"
        salt = os.urandom(16)
        salt_b64 = base64.b64encode(salt).decode("ascii")
        password_hash = base64.b64encode(hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
        )).decode("ascii")
        self.db.insert_user(
            user_id=str(uuid.uuid4()),
            username="testuser",
            password_hash=password_hash,
            salt=salt_b64,
        )
        # Login to get session cookie
        self.client.post(
            "/login",
            data={
                "username": "testuser",
                "password": "testpass"
            },
            follow_redirects=False,
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_clone_single_session_file_config(self) -> None:
        """Clone action creates a copy of a session file config with definitions."""
        # Create a session file config with some definitions
        config_id = str(uuid.uuid4())
        self.db.insert_session_file_config(config_id, "Test Config", "A test config")
        self.db.insert_session_file_definition(
            definition_id=str(uuid.uuid4()),
            config_id=config_id,
            filename="STATE.md",
            default_content="# State",
            description="State file",
            required=True,
            sync_on_exit=True,
            sort_order=1,
        )
        self.db.insert_session_file_definition(
            definition_id=str(uuid.uuid4()),
            config_id=config_id,
            filename="TODO.md",
            default_content="# TODO",
            description="Todo file",
            required=False,
            sync_on_exit=True,
            sort_order=2,
        )

        response = self.client.post(
            "/ui/session_file_configs/bulk-action",
            data={
                "action": "clone",
                "ids": [config_id]
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/ui/", response.headers["location"])

        # Verify the clone was created
        configs = self.db.list_session_file_configs()
        self.assertEqual(len(configs), 2)

        # Find the cloned config
        cloned = [c for c in configs if c.id != config_id][0]
        self.assertEqual(cloned.name, "Test Config2")
        self.assertEqual(cloned.description, "A test config")

        # Verify definitions were also cloned
        cloned_definitions = self.db.list_session_file_definitions(cloned.id)
        self.assertEqual(len(cloned_definitions), 2)

        filenames = {d.filename for d in cloned_definitions}
        self.assertIn("STATE.md", filenames)
        self.assertIn("TODO.md", filenames)

    def test_clone_multiple_session_file_configs(self) -> None:
        """Clone action creates copies of multiple configs."""
        config_id_1 = str(uuid.uuid4())
        config_id_2 = str(uuid.uuid4())
        self.db.insert_session_file_config(config_id_1, "Config Alpha", None)
        self.db.insert_session_file_config(config_id_2, "Config Beta", None)

        response = self.client.post(
            "/ui/session_file_configs/bulk-action",
            data={
                "action": "clone",
                "ids": [config_id_1, config_id_2],
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/ui/", response.headers["location"])

        configs = self.db.list_session_file_configs()
        self.assertEqual(len(configs), 4)

        names = {c.name for c in configs}
        self.assertIn("Config Alpha", names)
        self.assertIn("Config Alpha2", names)
        self.assertIn("Config Beta", names)
        self.assertIn("Config Beta2", names)

    def test_session_file_configs_page_shows_bulk_actions(self) -> None:
        """Session file configs list page includes bulk action UI elements."""
        response = self.client.get("/ui/session-file-configs")
        self.assertEqual(response.status_code, 200)

        # Check for bulk action elements
        self.assertIn("data-bulk-actions", response.text)
        self.assertIn("data-bulk-model", response.text)
        self.assertIn("data-select-all", response.text)
        self.assertIn("data-bulk-action-select", response.text)
        self.assertIn("data-bulk-action-go", response.text)
        self.assertIn("Clone", response.text)


if __name__ == "__main__":
    unittest.main()
