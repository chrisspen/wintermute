"""Tests for Agent LLM configuration."""

import tempfile
import unittest

from wintermute.db import Database
from wintermute.executor import Executor


class AgentLLMConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp_db.close()

    def test_insert_agent_with_llm_config(self) -> None:
        """Agent can be created with LLM configuration."""
        self.db.insert_agent(
            agent_id="agent-1",
            name="Test Agent",
            slug="test-agent",
            command="claude",
            session_mode="claude",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            llm_base_url="http://localhost:11434/v1",
            llm_api_key="test-key",
            llm_model="llama3.2",
        )
        agent = self.db.get_agent("agent-1")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.llm_base_url, "http://localhost:11434/v1")
        self.assertEqual(agent.llm_api_key, "test-key")
        self.assertEqual(agent.llm_model, "llama3.2")

    def test_insert_agent_without_llm_config(self) -> None:
        """Agent can be created without LLM configuration (defaults to None)."""
        self.db.insert_agent(
            agent_id="agent-2",
            name="No LLM Agent",
            slug="no-llm-agent",
            command="claude",
            session_mode="claude",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )
        agent = self.db.get_agent("agent-2")
        self.assertIsNotNone(agent)
        self.assertIsNone(agent.llm_base_url)
        self.assertIsNone(agent.llm_api_key)
        self.assertIsNone(agent.llm_model)

    def test_update_agent_llm_config(self) -> None:
        """Agent LLM configuration can be updated."""
        self.db.insert_agent(
            agent_id="agent-3",
            name="Update Agent",
            slug="update-agent",
            command="claude",
            session_mode="claude",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )
        self.db.update_agent(
            "agent-3",
            llm_base_url="https://api.openai.com/v1",
            llm_api_key="sk-test",
            llm_model="gpt-4",
        )
        agent = self.db.get_agent("agent-3")
        self.assertEqual(agent.llm_base_url, "https://api.openai.com/v1")
        self.assertEqual(agent.llm_api_key, "sk-test")
        self.assertEqual(agent.llm_model, "gpt-4")

    def test_list_agents_includes_llm_config(self) -> None:
        """list_agents returns agents with LLM configuration."""
        self.db.insert_agent(
            agent_id="agent-4",
            name="Listed Agent",
            slug="listed-agent",
            command="claude",
            session_mode="claude",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            llm_base_url="http://example.com/v1",
            llm_api_key="key123",
            llm_model="model-x",
        )
        agents = self.db.list_agents()
        agent = next((a for a in agents if a.id == "agent-4"), None)
        self.assertIsNotNone(agent)
        self.assertEqual(agent.llm_base_url, "http://example.com/v1")
        self.assertEqual(agent.llm_api_key, "key123")
        self.assertEqual(agent.llm_model, "model-x")

    def test_get_agent_by_slug_includes_llm_config(self) -> None:
        """get_agent_by_slug returns agent with LLM configuration."""
        self.db.insert_agent(
            agent_id="agent-5",
            name="Slug Agent",
            slug="slug-agent",
            command="claude",
            session_mode="claude",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            llm_base_url="http://slug.example.com/v1",
            llm_api_key="slug-key",
            llm_model="slug-model",
        )
        agent = self.db.get_agent_by_slug("slug-agent")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.llm_base_url, "http://slug.example.com/v1")
        self.assertEqual(agent.llm_api_key, "slug-key")
        self.assertEqual(agent.llm_model, "slug-model")


class ExecutorOverrideTests(unittest.TestCase):
    def test_executor_uses_defaults(self) -> None:
        """Executor uses its own defaults when no overrides provided."""
        executor = Executor(
            base_url="http://default.example.com/v1",
            api_key="default-key",
            model="default-model",
        )
        # We can't easily test the actual call without mocking, but we can verify
        # the executor stores the defaults
        self.assertEqual(executor.base_url, "http://default.example.com/v1")
        self.assertEqual(executor.api_key, "default-key")
        self.assertEqual(executor.model, "default-model")

    def test_executor_signature_accepts_overrides(self) -> None:
        """Executor.decide_next_action accepts override parameters."""
        executor = Executor(
            base_url="http://default.example.com/v1",
            api_key="default-key",
            model="default-model",
        )
        # Verify the method signature accepts the new parameters
        import inspect
        sig = inspect.signature(executor.decide_next_action)
        param_names = list(sig.parameters.keys())
        self.assertIn("base_url", param_names)
        self.assertIn("api_key", param_names)
        self.assertIn("model", param_names)


if __name__ == "__main__":
    unittest.main()
