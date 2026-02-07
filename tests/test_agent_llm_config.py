"""Tests for Agent LLM configuration."""

import pytest

from wintermute.models import Agent
from wintermute.executor import Executor
from wintermute.utils import utc_now


@pytest.mark.django_db
class TestAgentLLMConfig:
    """Tests for Agent LLM configuration."""

    def test_insert_agent_with_llm_config(self):
        """Agent can be created with LLM configuration."""
        now = utc_now()
        agent = Agent.objects.create(
            id="agent-1",
            name="Test Agent",
            slug="test-agent",
            command="claude",
            session_mode="claude",
            llm_base_url="http://localhost:11434/v1",
            llm_api_key="test-key",
            llm_model="llama3.2",
            created_at=now,
            updated_at=now,
        )
        assert agent.llm_base_url == "http://localhost:11434/v1"
        assert agent.llm_api_key == "test-key"
        assert agent.llm_model == "llama3.2"

    def test_insert_agent_without_llm_config(self):
        """Agent can be created without LLM configuration (defaults to None)."""
        now = utc_now()
        agent = Agent.objects.create(
            id="agent-2",
            name="No LLM Agent",
            slug="no-llm-agent",
            command="claude",
            session_mode="claude",
            created_at=now,
            updated_at=now,
        )
        assert agent.llm_base_url is None
        assert agent.llm_api_key is None
        assert agent.llm_model is None

    def test_update_agent_llm_config(self):
        """Agent LLM configuration can be updated."""
        now = utc_now()
        agent = Agent.objects.create(
            id="agent-3",
            name="Update Agent",
            slug="update-agent",
            command="claude",
            session_mode="claude",
            created_at=now,
            updated_at=now,
        )
        agent.llm_base_url = "https://api.openai.com/v1"
        agent.llm_api_key = "sk-test"
        agent.llm_model = "gpt-4"
        agent.save()

        agent.refresh_from_db()
        assert agent.llm_base_url == "https://api.openai.com/v1"
        assert agent.llm_api_key == "sk-test"
        assert agent.llm_model == "gpt-4"

    def test_list_agents_includes_llm_config(self):
        """list_agents returns agents with LLM configuration."""
        now = utc_now()
        Agent.objects.create(
            id="agent-4",
            name="Listed Agent",
            slug="listed-agent",
            command="claude",
            session_mode="claude",
            llm_base_url="http://example.com/v1",
            llm_api_key="key123",
            llm_model="model-x",
            created_at=now,
            updated_at=now,
        )
        agents = list(Agent.objects.all())
        agent = next((a for a in agents if a.id == "agent-4"), None)
        assert agent is not None
        assert agent.llm_base_url == "http://example.com/v1"
        assert agent.llm_api_key == "key123"
        assert agent.llm_model == "model-x"

    def test_get_agent_by_slug_includes_llm_config(self):
        """get_agent_by_slug returns agent with LLM configuration."""
        now = utc_now()
        Agent.objects.create(
            id="agent-5",
            name="Slug Agent",
            slug="slug-agent",
            command="claude",
            session_mode="claude",
            llm_base_url="http://slug.example.com/v1",
            llm_api_key="slug-key",
            llm_model="slug-model",
            created_at=now,
            updated_at=now,
        )
        agent = Agent.objects.filter(slug="slug-agent").first()
        assert agent is not None
        assert agent.llm_base_url == "http://slug.example.com/v1"
        assert agent.llm_api_key == "slug-key"
        assert agent.llm_model == "slug-model"


class TestExecutorOverrides:
    """Tests for Executor override parameters."""

    def test_executor_uses_defaults(self):
        """Executor uses its own defaults when no overrides provided."""
        executor = Executor(
            base_url="http://default.example.com/v1",
            api_key="default-key",
            model="default-model",
        )
        assert executor.base_url == "http://default.example.com/v1"
        assert executor.api_key == "default-key"
        assert executor.model == "default-model"

    def test_executor_signature_accepts_overrides(self):
        """Executor.decide_next_action accepts override parameters."""
        import inspect

        executor = Executor(
            base_url="http://default.example.com/v1",
            api_key="default-key",
            model="default-model",
        )
        sig = inspect.signature(executor.decide_next_action)
        param_names = list(sig.parameters.keys())
        assert "base_url" in param_names
        assert "api_key" in param_names
        assert "model" in param_names
