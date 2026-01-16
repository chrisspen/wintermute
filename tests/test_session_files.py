"""Unit tests for SessionFileConfig, SessionFileDefinition, SessionFile, and Channel CRUD operations."""

import os
import tempfile
import unittest
import uuid

from wintermute.db import Database


class SessionFileConfigCRUDTests(unittest.TestCase):
    """Tests for SessionFileConfig CRUD operations."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_insert_session_file_config(self) -> None:
        config_id = str(uuid.uuid4())
        self.db.insert_session_file_config(
            config_id=config_id,
            name="Test Config",
            description="A test config",
        )
        config = self.db.get_session_file_config(config_id)
        self.assertIsNotNone(config)
        self.assertEqual(config.name, "Test Config")
        self.assertEqual(config.description, "A test config")

    def test_list_session_file_configs(self) -> None:
        # There's a default config created by the migration
        configs = self.db.list_session_file_configs()
        initial_count = len(configs)

        config_id = str(uuid.uuid4())
        self.db.insert_session_file_config(
            config_id=config_id,
            name="New Config",
        )
        configs = self.db.list_session_file_configs()
        self.assertEqual(len(configs), initial_count + 1)

    def test_update_session_file_config(self) -> None:
        config_id = str(uuid.uuid4())
        self.db.insert_session_file_config(
            config_id=config_id,
            name="Original Name",
        )
        self.db.update_session_file_config(
            config_id=config_id,
            name="Updated Name",
            description="Updated description",
        )
        config = self.db.get_session_file_config(config_id)
        self.assertEqual(config.name, "Updated Name")
        self.assertEqual(config.description, "Updated description")

    def test_delete_session_file_config(self) -> None:
        config_id = str(uuid.uuid4())
        self.db.insert_session_file_config(
            config_id=config_id,
            name="To Be Deleted",
        )
        self.db.delete_session_file_config(config_id)
        config = self.db.get_session_file_config(config_id)
        self.assertIsNone(config)


class SessionFileDefinitionCRUDTests(unittest.TestCase):
    """Tests for SessionFileDefinition CRUD operations."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        # Create a config to attach definitions to
        self.config_id = str(uuid.uuid4())
        self.db.insert_session_file_config(
            config_id=self.config_id,
            name="Test Config",
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_insert_session_file_definition(self) -> None:
        def_id = str(uuid.uuid4())
        self.db.insert_session_file_definition(
            definition_id=def_id,
            config_id=self.config_id,
            filename="TEST.md",
            description="A test file",
            default_content="# Test\n\nContent here.",
            required=True,
            sync_on_exit=True,
            sort_order=0,
        )
        definition = self.db.get_session_file_definition(def_id)
        self.assertIsNotNone(definition)
        self.assertEqual(definition.filename, "TEST.md")
        self.assertEqual(definition.description, "A test file")
        self.assertEqual(definition.default_content, "# Test\n\nContent here.")
        self.assertTrue(definition.required)
        self.assertTrue(definition.sync_on_exit)

    def test_list_session_file_definitions(self) -> None:
        # Add two definitions
        for i in range(2):
            self.db.insert_session_file_definition(
                definition_id=str(uuid.uuid4()),
                config_id=self.config_id,
                filename=f"FILE{i}.md",
                default_content=f"Content {i}",
                sort_order=i,
            )
        definitions = self.db.list_session_file_definitions(self.config_id)
        self.assertEqual(len(definitions), 2)
        # Should be ordered by sort_order
        self.assertEqual(definitions[0].filename, "FILE0.md")
        self.assertEqual(definitions[1].filename, "FILE1.md")

    def test_update_session_file_definition(self) -> None:
        def_id = str(uuid.uuid4())
        self.db.insert_session_file_definition(
            definition_id=def_id,
            config_id=self.config_id,
            filename="ORIGINAL.md",
            default_content="Original content",
        )
        self.db.update_session_file_definition(
            definition_id=def_id,
            filename="UPDATED.md",
            default_content="Updated content",
            required=True,
        )
        definition = self.db.get_session_file_definition(def_id)
        self.assertEqual(definition.filename, "UPDATED.md")
        self.assertEqual(definition.default_content, "Updated content")
        self.assertTrue(definition.required)

    def test_delete_session_file_definition(self) -> None:
        def_id = str(uuid.uuid4())
        self.db.insert_session_file_definition(
            definition_id=def_id,
            config_id=self.config_id,
            filename="DELETE_ME.md",
            default_content="",
        )
        self.db.delete_session_file_definition(def_id)
        definition = self.db.get_session_file_definition(def_id)
        self.assertIsNone(definition)


class SessionFileCRUDTests(unittest.TestCase):
    """Tests for SessionFile (per-agent) CRUD operations."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        # Create config and definition
        self.config_id = str(uuid.uuid4())
        self.db.insert_session_file_config(
            config_id=self.config_id,
            name="Test Config",
        )
        self.definition_id = str(uuid.uuid4())
        self.db.insert_session_file_definition(
            definition_id=self.definition_id,
            config_id=self.config_id,
            filename="STATE.md",
            default_content="# State",
        )
        # Create an agent
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent",
            slug="test-agent",
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

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_insert_session_file(self) -> None:
        file_id = str(uuid.uuid4())
        self.db.insert_session_file(
            file_id=file_id,
            agent_id=self.agent_id,
            definition_id=self.definition_id,
            content="# State\n\nMy state content.",
        )
        session_file = self.db.get_session_file(file_id)
        self.assertIsNotNone(session_file)
        self.assertEqual(session_file.agent_id, self.agent_id)
        self.assertEqual(session_file.definition_id, self.definition_id)
        self.assertEqual(session_file.content, "# State\n\nMy state content.")

    def test_get_session_file_by_definition(self) -> None:
        file_id = str(uuid.uuid4())
        self.db.insert_session_file(
            file_id=file_id,
            agent_id=self.agent_id,
            definition_id=self.definition_id,
            content="Test content",
        )
        session_file = self.db.get_session_file_by_definition(
            self.agent_id, self.definition_id
        )
        self.assertIsNotNone(session_file)
        self.assertEqual(session_file.id, file_id)

    def test_list_session_files(self) -> None:
        # Create another definition
        def2_id = str(uuid.uuid4())
        self.db.insert_session_file_definition(
            definition_id=def2_id,
            config_id=self.config_id,
            filename="TODO.md",
            default_content="# TODO",
        )
        # Create files for both definitions
        self.db.insert_session_file(
            file_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            definition_id=self.definition_id,
            content="State content",
        )
        self.db.insert_session_file(
            file_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            definition_id=def2_id,
            content="TODO content",
        )
        files = self.db.list_session_files(self.agent_id)
        self.assertEqual(len(files), 2)

    def test_update_session_file(self) -> None:
        file_id = str(uuid.uuid4())
        self.db.insert_session_file(
            file_id=file_id,
            agent_id=self.agent_id,
            definition_id=self.definition_id,
            content="Original",
        )
        self.db.update_session_file(file_id=file_id, content="Updated content")
        session_file = self.db.get_session_file(file_id)
        self.assertEqual(session_file.content, "Updated content")

    def test_delete_session_file(self) -> None:
        file_id = str(uuid.uuid4())
        self.db.insert_session_file(
            file_id=file_id,
            agent_id=self.agent_id,
            definition_id=self.definition_id,
            content="To delete",
        )
        self.db.delete_session_file(file_id)
        session_file = self.db.get_session_file(file_id)
        self.assertIsNone(session_file)

    def test_delete_session_files_for_agent(self) -> None:
        # Create multiple files
        for i in range(3):
            def_id = str(uuid.uuid4())
            self.db.insert_session_file_definition(
                definition_id=def_id,
                config_id=self.config_id,
                filename=f"FILE{i}.md",
                default_content="",
            )
            self.db.insert_session_file(
                file_id=str(uuid.uuid4()),
                agent_id=self.agent_id,
                definition_id=def_id,
                content=f"Content {i}",
            )
        files = self.db.list_session_files(self.agent_id)
        self.assertGreater(len(files), 0)
        self.db.delete_session_files_for_agent(self.agent_id)
        files = self.db.list_session_files(self.agent_id)
        self.assertEqual(len(files), 0)


class ChannelCRUDTests(unittest.TestCase):
    """Tests for Channel CRUD operations."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        # Create an agent
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent",
            slug="test-agent",
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

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_insert_channel(self) -> None:
        channel_id = str(uuid.uuid4())
        self.db.insert_channel(
            channel_id=channel_id,
            agent_id=self.agent_id,
            channel_type="slack",
            name="agent/vm",
            external_channel_id="C12345",
            enabled=True,
        )
        channel = self.db.get_channel(channel_id)
        self.assertIsNotNone(channel)
        self.assertEqual(channel.agent_id, self.agent_id)
        self.assertEqual(channel.type, "slack")
        self.assertEqual(channel.name, "agent/vm")
        self.assertEqual(channel.external_channel_id, "C12345")
        self.assertTrue(channel.enabled)

    def test_list_channels_for_agent(self) -> None:
        # Create channels for our agent
        for i in range(2):
            self.db.insert_channel(
                channel_id=str(uuid.uuid4()),
                agent_id=self.agent_id,
                channel_type="slack",
                name=f"channel{i}",
            )
        channels = self.db.list_channels(agent_id=self.agent_id)
        self.assertEqual(len(channels), 2)

    def test_list_all_channels(self) -> None:
        # Create another agent with channels
        agent2_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent2_id,
            name="Agent 2",
            slug="agent-2",
            command="echo test2",
            session_mode="tmux",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )
        self.db.insert_channel(
            channel_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            channel_type="slack",
            name="channel1",
        )
        self.db.insert_channel(
            channel_id=str(uuid.uuid4()),
            agent_id=agent2_id,
            channel_type="telegram",
            name="channel2",
        )
        channels = self.db.list_channels()
        self.assertEqual(len(channels), 2)

    def test_get_channel_by_external_id(self) -> None:
        channel_id = str(uuid.uuid4())
        self.db.insert_channel(
            channel_id=channel_id,
            agent_id=self.agent_id,
            channel_type="slack",
            name="test",
            external_channel_id="C99999",
        )
        channel = self.db.get_channel_by_external_id("slack", "C99999")
        self.assertIsNotNone(channel)
        self.assertEqual(channel.id, channel_id)

    def test_update_channel(self) -> None:
        channel_id = str(uuid.uuid4())
        self.db.insert_channel(
            channel_id=channel_id,
            agent_id=self.agent_id,
            channel_type="slack",
            name="original",
            enabled=True,
        )
        self.db.update_channel(
            channel_id=channel_id,
            name="updated",
            external_channel_id="C_UPDATED",
            enabled=False,
        )
        channel = self.db.get_channel(channel_id)
        self.assertEqual(channel.name, "updated")
        self.assertEqual(channel.external_channel_id, "C_UPDATED")
        self.assertFalse(channel.enabled)

    def test_delete_channel(self) -> None:
        channel_id = str(uuid.uuid4())
        self.db.insert_channel(
            channel_id=channel_id,
            agent_id=self.agent_id,
            channel_type="slack",
            name="to_delete",
        )
        self.db.delete_channel(channel_id)
        channel = self.db.get_channel(channel_id)
        self.assertIsNone(channel)


class AgentSessionStandaloneTests(unittest.TestCase):
    """Tests for standalone agent sessions (no project_id)."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        # Create an agent
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent",
            slug="test-agent",
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
        # Create a VM target
        self.vm_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=self.vm_id,
            name="Test VM",
            host="test.local",
            user="testuser",
            port=22,
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_insert_standalone_session(self) -> None:
        """Test that agent sessions can be created without a project_id."""
        session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=session_id,
            project_id=None,  # No project - standalone session
            agent_id=self.agent_id,
            ticket_id=None,
            status="running",
            repo_path="/tmp/repo",
            thread_ts=None,
            initial_prompt="Hello, agent!",
            workspace_path="/tmp/workspace",
        )
        session = self.db.get_session(session_id)
        self.assertIsNotNone(session)
        self.assertEqual(session.agent_id, self.agent_id)
        self.assertIsNone(session.project_id)
        self.assertEqual(session.initial_prompt, "Hello, agent!")
        self.assertEqual(session.workspace_path, "/tmp/workspace")


if __name__ == "__main__":
    unittest.main()
