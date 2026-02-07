"""Tests for repo resource acquisition, especially the 'local' repo mode."""

import uuid

import pytest

from wintermute.models import Agent, AgentSession, Project, RepoResource
from wintermute.models import Project as ProjectRecord
from wintermute.services.database import Database


@pytest.mark.django_db(transaction=True)
class TestRepoResourceLocalMode:
    """Tests for acquiring repo resources in 'local' mode."""

    def setup_method(self) -> None:
        self.db = Database(":memory:") # Path ignored, uses Django's test DB

    def teardown_method(self) -> None:
        RepoResource.objects.all().delete()
        AgentSession.objects.all().delete()
        Agent.objects.all().delete()
        Project.objects.all().delete()

    def _create_project(self, repo_mode: str, repo_path: str | None = None) -> ProjectRecord:
        """Helper to create a project with given repo mode."""
        project_id = str(uuid.uuid4())
        self.db.insert_project(
            project_id=project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
            repo_mode=repo_mode,
            repo_path=repo_path,
        )
        project = self.db.get_project(project_id)
        assert project is not None
        return project

    def _create_agent(self) -> str:
        """Helper to create an agent for tests."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="cli",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )
        return agent_id

    def _create_running_session(self, session_id: str, project_id: str) -> None:
        """Helper to create a running session (needed for blocking tests)."""
        agent_id = self._create_agent()
        self.db.insert_session(
            session_id=session_id,
            project_id=project_id,
            agent_id=agent_id,
            ticket_id=None,
            status="running",
            repo_path="/tmp/test",
            thread_ts=None,
        )

    def test_local_mode_acquires_resource_without_repo_path(self) -> None:
        """Local mode should work without repo_path configured."""
        project = self._create_project(repo_mode="local")
        assert project.repo_path is None

        resource, error = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-1",
            agent_id=None,
        )

        assert error is None
        assert resource is not None
        assert resource.repo_mode == "local"
        assert resource.path == f"local:{project.id}"
        assert resource.status == "in_use"
        assert resource.session_id == "test-session-1"

    def test_local_mode_reuses_existing_available_resource(self) -> None:
        """Local mode should reuse existing available resource."""
        project = self._create_project(repo_mode="local")

        # First acquisition
        resource1, error1 = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-1",
            agent_id=None,
        )
        assert error1 is None
        assert resource1 is not None

        # Release the resource using release_repo_resource_for_session
        self.db.release_repo_resource_for_session("test-session-1")

        # Second acquisition should reuse the same resource
        resource2, error2 = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-2",
            agent_id=None,
        )
        assert error2 is None
        assert resource2 is not None
        assert resource1.id == resource2.id
        assert resource2.session_id == "test-session-2"

    def test_local_mode_blocks_when_in_use(self) -> None:
        """Local mode should block acquisition when resource is in use by running session."""
        project = self._create_project(repo_mode="local")

        # Create a running session for first acquisition
        self._create_running_session("test-session-1", project.id)

        # First acquisition
        resource1, error1 = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-1",
            agent_id=None,
        )
        assert error1 is None
        assert resource1 is not None

        # Second acquisition should fail (session 1 is still running)
        resource2, error2 = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-2",
            agent_id=None,
        )
        assert resource2 is None
        assert error2 == "local repo already in use"


@pytest.mark.django_db(transaction=True)
class TestRepoResourceMirrorMode:
    """Tests for acquiring repo resources in 'mirror' mode."""

    def setup_method(self) -> None:
        self.db = Database(":memory:") # Path ignored, uses Django's test DB

    def teardown_method(self) -> None:
        RepoResource.objects.all().delete()
        AgentSession.objects.all().delete()
        Agent.objects.all().delete()
        Project.objects.all().delete()

    def _create_project(self, repo_mode: str, repo_path: str | None = None) -> ProjectRecord:
        """Helper to create a project with given repo mode."""
        project_id = str(uuid.uuid4())
        self.db.insert_project(
            project_id=project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
            repo_mode=repo_mode,
            repo_path=repo_path,
        )
        project = self.db.get_project(project_id)
        assert project is not None
        return project

    def _create_agent(self) -> str:
        """Helper to create an agent for tests."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="cli",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )
        return agent_id

    def _create_running_session(self, session_id: str, project_id: str) -> None:
        """Helper to create a running session (needed for blocking tests)."""
        agent_id = self._create_agent()
        self.db.insert_session(
            session_id=session_id,
            project_id=project_id,
            agent_id=agent_id,
            ticket_id=None,
            status="running",
            repo_path="/tmp/test",
            thread_ts=None,
        )

    def test_mirror_mode_requires_repo_path(self) -> None:
        """Mirror mode should fail without repo_path configured."""
        project = self._create_project(repo_mode="mirror")
        assert project.repo_path is None

        resource, error = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-1",
            agent_id=None,
        )

        assert resource is None
        assert error == "mirror path not configured"

    def test_mirror_mode_acquires_resource_with_repo_path(self) -> None:
        """Mirror mode should work with repo_path configured."""
        project = self._create_project(repo_mode="mirror", repo_path="/home/user/git/project")

        resource, error = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-1",
            agent_id=None,
        )

        assert error is None
        assert resource is not None
        assert resource.repo_mode == "mirror"
        assert resource.path == "/home/user/git/project"
        assert resource.status == "in_use"

    def test_mirror_mode_blocks_when_in_use(self) -> None:
        """Mirror mode should block acquisition when resource is in use by running session."""
        project = self._create_project(repo_mode="mirror", repo_path="/home/user/git/project")

        # Create a running session for first acquisition
        self._create_running_session("test-session-1", project.id)

        # First acquisition
        resource1, error1 = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-1",
            agent_id=None,
        )
        assert error1 is None

        # Second acquisition should fail (session 1 is still running)
        resource2, error2 = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-2",
            agent_id=None,
        )
        assert resource2 is None
        assert error2 == "mirror repo already in use"


@pytest.mark.django_db(transaction=True)
class TestRepoResourceCloneMode:
    """Tests for acquiring repo resources in 'clone' mode."""

    def setup_method(self) -> None:
        self.db = Database(":memory:") # Path ignored, uses Django's test DB

    def teardown_method(self) -> None:
        RepoResource.objects.all().delete()
        AgentSession.objects.all().delete()
        Agent.objects.all().delete()
        Project.objects.all().delete()

    def _create_project(self, repo_mode: str, repo_path: str | None = None, max_repo_resources: int = 3) -> ProjectRecord:
        """Helper to create a project with given repo mode."""
        project_id = str(uuid.uuid4())
        self.db.insert_project(
            project_id=project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
            repo_mode=repo_mode,
            repo_path=repo_path,
            max_repo_resources=max_repo_resources,
        )
        project = self.db.get_project(project_id)
        assert project is not None
        return project

    def _create_agent(self) -> str:
        """Helper to create an agent for tests."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="Test Agent",
            slug=f"test-agent-{agent_id[:8]}",
            command="echo test",
            session_mode="cli",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )
        return agent_id

    def _create_running_session(self, session_id: str, project_id: str) -> None:
        """Helper to create a running session (needed for blocking tests)."""
        agent_id = self._create_agent()
        self.db.insert_session(
            session_id=session_id,
            project_id=project_id,
            agent_id=agent_id,
            ticket_id=None,
            status="running",
            repo_path="/tmp/test",
            thread_ts=None,
        )

    def test_clone_mode_requires_repo_path(self) -> None:
        """Clone mode should fail without repo_path configured."""
        project = self._create_project(repo_mode="clone")
        assert project.repo_path is None

        resource, error = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-1",
            agent_id=None,
        )

        assert resource is None
        assert error == "repo path not configured"

    def test_clone_mode_creates_unique_paths(self) -> None:
        """Clone mode should create unique paths for each session."""
        project = self._create_project(repo_mode="clone", repo_path="/home/user/git/project", max_repo_resources=3)

        # Create running sessions for both acquisitions
        self._create_running_session("test-session-1", project.id)
        self._create_running_session("test-session-2", project.id)

        # First acquisition
        resource1, error1 = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-1",
            agent_id=None,
        )
        assert error1 is None
        assert resource1 is not None

        # Second acquisition should create a new resource with different path
        resource2, error2 = self.db.acquire_repo_resource(
            project=project,
            session_id="test-session-2",
            agent_id=None,
        )
        assert error2 is None
        assert resource2 is not None
        assert resource1.id != resource2.id
        assert resource1.path != resource2.path
        assert "test-session-2" in resource2.path


if __name__ == "__main__":
    pytest.main([__file__])
