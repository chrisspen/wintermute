"""Tests for agent metrics and memory management functionality."""

import tempfile
import unittest
import uuid
from unittest.mock import patch, MagicMock

from wintermute.db import Database, VMTargetRecord, AgentRecord


class MetricDefinitionCRUDTests(unittest.TestCase):
    """Tests for MetricDefinition CRUD operations."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp_db.close()

    def test_create_metric_definition(self) -> None:
        """Should create a metric definition."""
        definition_id = str(uuid.uuid4())
        self.db.insert_metric_definition(
            definition_id=definition_id,
            metric_type="MEMORY_USAGE",
            recording_frequency_minutes=5,
            enabled=True,
        )
        defn = self.db.get_metric_definition(definition_id)
        self.assertIsNotNone(defn)
        self.assertEqual(defn.metric_type, "MEMORY_USAGE")
        self.assertEqual(defn.recording_frequency_minutes, 5)
        self.assertTrue(defn.enabled)

    def test_get_metric_definition(self) -> None:
        """Should get a metric definition by ID."""
        definition_id = str(uuid.uuid4())
        self.db.insert_metric_definition(
            definition_id=definition_id,
            metric_type="CPU_USAGE",
            recording_frequency_minutes=10,
            enabled=False,
        )
        fetched = self.db.get_metric_definition(definition_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.metric_type, "CPU_USAGE")
        self.assertEqual(fetched.recording_frequency_minutes, 10)
        self.assertFalse(fetched.enabled)

    def test_list_metric_definitions(self) -> None:
        """Should list all metric definitions."""
        self.db.insert_metric_definition(
            definition_id=str(uuid.uuid4()),
            metric_type="MEMORY_USAGE",
            recording_frequency_minutes=5,
            enabled=True,
        )
        self.db.insert_metric_definition(
            definition_id=str(uuid.uuid4()),
            metric_type="CPU_USAGE",
            recording_frequency_minutes=10,
            enabled=False,
        )
        definitions = self.db.list_metric_definitions()
        self.assertEqual(len(definitions), 2)

    def test_update_metric_definition(self) -> None:
        """Should update a metric definition."""
        definition_id = str(uuid.uuid4())
        self.db.insert_metric_definition(
            definition_id=definition_id,
            metric_type="MEMORY_USAGE",
            recording_frequency_minutes=5,
            enabled=True,
        )
        self.db.update_metric_definition(
            definition_id,
            metric_type="DISK_USAGE",
            recording_frequency_minutes=15,
            enabled=False,
        )
        updated = self.db.get_metric_definition(definition_id)
        self.assertEqual(updated.metric_type, "DISK_USAGE")
        self.assertEqual(updated.recording_frequency_minutes, 15)
        self.assertFalse(updated.enabled)

    def test_delete_metric_definition(self) -> None:
        """Should delete a metric definition."""
        definition_id = str(uuid.uuid4())
        self.db.insert_metric_definition(
            definition_id=definition_id,
            metric_type="MEMORY_USAGE",
            recording_frequency_minutes=5,
            enabled=True,
        )
        self.db.delete_metric_definition(definition_id)
        self.assertIsNone(self.db.get_metric_definition(definition_id))


class AgentMetricsLogCRUDTests(unittest.TestCase):
    """Tests for AgentMetricsLog CRUD operations."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        # Create test agent and metric definition
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
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
        self.metric_def_id = str(uuid.uuid4())
        self.db.insert_metric_definition(
            definition_id=self.metric_def_id,
            metric_type="MEMORY_USAGE",
            recording_frequency_minutes=5,
            enabled=True,
        )

    def tearDown(self) -> None:
        self.temp_db.close()

    def test_create_agent_metrics_log(self) -> None:
        """Should create an agent metrics log entry."""
        log_id = str(uuid.uuid4())
        self.db.insert_agent_metrics_log(
            log_id=log_id,
            agent_id=self.agent_id,
            metric_definition_id=self.metric_def_id,
            value=512.5,
        )
        log = self.db.get_agent_metrics_log(log_id)
        self.assertIsNotNone(log)
        self.assertEqual(log.agent_id, self.agent_id)
        self.assertEqual(log.metric_definition_id, self.metric_def_id)
        self.assertEqual(log.value, 512.5)

    def test_list_agent_metrics_logs(self) -> None:
        """Should list agent metrics logs."""
        self.db.insert_agent_metrics_log(
            log_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            metric_definition_id=self.metric_def_id,
            value=512.5,
        )
        self.db.insert_agent_metrics_log(
            log_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            metric_definition_id=self.metric_def_id,
            value=600.0,
        )
        logs = self.db.list_agent_metrics_logs()
        self.assertEqual(len(logs), 2)

    def test_list_agent_metrics_logs_by_agent(self) -> None:
        """Should filter metrics logs by agent ID."""
        agent_id_2 = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id_2,
            name="Test Agent 2",
            slug="test-agent-2",
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
        self.db.insert_agent_metrics_log(
            log_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            metric_definition_id=self.metric_def_id,
            value=512.5,
        )
        self.db.insert_agent_metrics_log(
            log_id=str(uuid.uuid4()),
            agent_id=agent_id_2,
            metric_definition_id=self.metric_def_id,
            value=300.0,
        )
        logs = self.db.list_agent_metrics_logs(agent_id=self.agent_id)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].agent_id, self.agent_id)

    def test_list_agent_metrics_logs_with_limit(self) -> None:
        """Should respect limit parameter."""
        for i in range(10):
            self.db.insert_agent_metrics_log(
                log_id=str(uuid.uuid4()),
                agent_id=self.agent_id,
                metric_definition_id=self.metric_def_id,
                value=float(i * 100),
            )
        logs = self.db.list_agent_metrics_logs(limit=5)
        self.assertEqual(len(logs), 5)


class AgentAverageMemoryUsageTests(unittest.TestCase):
    """Tests for agent average memory usage calculation."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        # Create test agent
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
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
        # Create MEMORY_USAGE metric definition
        self.memory_def_id = str(uuid.uuid4())
        self.db.insert_metric_definition(
            definition_id=self.memory_def_id,
            metric_type="MEMORY_USAGE",
            recording_frequency_minutes=5,
            enabled=True,
        )

    def tearDown(self) -> None:
        self.temp_db.close()

    def test_get_agent_average_memory_usage_no_logs(self) -> None:
        """Should return None when no metrics logs exist."""
        avg = self.db.get_agent_average_memory_usage(self.agent_id)
        self.assertIsNone(avg)

    def test_get_agent_average_memory_usage_with_logs(self) -> None:
        """Should calculate average from metrics logs."""
        self.db.insert_agent_metrics_log(
            log_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            metric_definition_id=self.memory_def_id,
            value=400.0,
        )
        self.db.insert_agent_metrics_log(
            log_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            metric_definition_id=self.memory_def_id,
            value=600.0,
        )
        self.db.insert_agent_metrics_log(
            log_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            metric_definition_id=self.memory_def_id,
            value=800.0,
        )
        avg = self.db.get_agent_average_memory_usage(self.agent_id)
        self.assertEqual(avg, 600.0) # (400 + 600 + 800) / 3

    def test_refresh_agent_average_memory_usage(self) -> None:
        """Should update agent's average_memory_usage_mb field."""
        self.db.insert_agent_metrics_log(
            log_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            metric_definition_id=self.memory_def_id,
            value=500.0,
        )
        self.db.insert_agent_metrics_log(
            log_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            metric_definition_id=self.memory_def_id,
            value=700.0,
        )
        self.db.refresh_agent_average_memory_usage(self.agent_id)
        agent = self.db.get_agent(self.agent_id)
        self.assertEqual(agent.average_memory_usage_mb, 600)

    def test_agent_default_average_memory_usage(self) -> None:
        """Agent should have default average_memory_usage_mb of 1000."""
        agent = self.db.get_agent(self.agent_id)
        self.assertEqual(agent.average_memory_usage_mb, 1000)


class VMTargetReserveMemoryTests(unittest.TestCase):
    """Tests for VM target required_reserve_memory_gb field."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp_db.close()

    def test_vm_target_default_reserve_memory(self) -> None:
        """VM target should have default required_reserve_memory_gb of 0.0."""
        vm_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=vm_id,
            name="Test VM",
            host="localhost",
            user="testuser",
            port=22,
        )
        vm = self.db.get_vm_target(vm_id)
        self.assertEqual(vm.required_reserve_memory_gb, 0.0)

    def test_update_vm_target_reserve_memory(self) -> None:
        """Should update required_reserve_memory_gb field."""
        vm_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=vm_id,
            name="Test VM",
            host="localhost",
            user="testuser",
            port=22,
        )
        self.db.update_vm_target(
            vm_id,
            name="Test VM",
            host="localhost",
            user="testuser",
            port=22,
            required_reserve_memory_gb=2.5,
        )
        vm = self.db.get_vm_target(vm_id)
        self.assertEqual(vm.required_reserve_memory_gb, 2.5)


class MemoryCheckTests(unittest.TestCase):
    """Tests for memory availability check functions."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp_db.close()

    @patch("wintermute.runner.subprocess.run")
    def test_check_vm_memory_available_sufficient(self, mock_run: MagicMock) -> None:
        """Should return True when memory is sufficient."""
        from wintermute.runner import check_vm_memory_available, SSHSpec

        # Mock ssh returning 8000 MB available
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="8000\n",
        )

        spec = SSHSpec(host="localhost", user="test", port=22, options=[])
        vm = VMTargetRecord(
            id="vm1",
            name="Test VM",
            host="localhost",
            user="test",
            port=22,
            required_reserve_memory_gb=2.0,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        agent = AgentRecord(
            id="agent1",
            name="Test Agent",
            slug="test-agent",
            command="echo",
            session_mode="cli",
            vm_target_id="vm1",
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            llm_base_url=None,
            llm_api_key=None,
            llm_model=None,
            session_file_config_id=None,
            average_memory_usage_mb=1000,
            initial_prompt=None,
            working_directory=None,
            session_directory=None,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )

        # 8000 - 1000 = 7000 > 2048 (2 GB), so should be OK
        ok, error = check_vm_memory_available(spec, vm, agent)
        self.assertTrue(ok)
        self.assertEqual(error, "")

    @patch("wintermute.runner.subprocess.run")
    def test_check_vm_memory_available_insufficient(self, mock_run: MagicMock) -> None:
        """Should return False when memory is insufficient."""
        from wintermute.runner import check_vm_memory_available, SSHSpec

        # Mock ssh returning 2500 MB available
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="2500\n",
        )

        spec = SSHSpec(host="localhost", user="test", port=22, options=[])
        vm = VMTargetRecord(
            id="vm1",
            name="Test VM",
            host="localhost",
            user="test",
            port=22,
            required_reserve_memory_gb=2.0,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        agent = AgentRecord(
            id="agent1",
            name="Test Agent",
            slug="test-agent",
            command="echo",
            session_mode="cli",
            vm_target_id="vm1",
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            llm_base_url=None,
            llm_api_key=None,
            llm_model=None,
            session_file_config_id=None,
            average_memory_usage_mb=1000,
            initial_prompt=None,
            working_directory=None,
            session_directory=None,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )

        # 2500 - 1000 = 1500 < 2048 (2 GB), so should fail
        ok, error = check_vm_memory_available(spec, vm, agent)
        self.assertFalse(ok)
        self.assertIn("Insufficient memory", error)
        self.assertIn("Test VM", error)

    @patch("wintermute.runner.subprocess.run")
    def test_check_vm_memory_available_ssh_failure(self, mock_run: MagicMock) -> None:
        """Should return True (allow start) when SSH fails."""
        from wintermute.runner import check_vm_memory_available, SSHSpec

        # Mock ssh failing
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
        )

        spec = SSHSpec(host="localhost", user="test", port=22, options=[])
        vm = VMTargetRecord(
            id="vm1",
            name="Test VM",
            host="localhost",
            user="test",
            port=22,
            required_reserve_memory_gb=2.0,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        agent = AgentRecord(
            id="agent1",
            name="Test Agent",
            slug="test-agent",
            command="echo",
            session_mode="cli",
            vm_target_id="vm1",
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            llm_base_url=None,
            llm_api_key=None,
            llm_model=None,
            session_file_config_id=None,
            average_memory_usage_mb=1000,
            initial_prompt=None,
            working_directory=None,
            session_directory=None,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )

        # When SSH fails, should allow start (return True)
        ok, error = check_vm_memory_available(spec, vm, agent)
        self.assertTrue(ok)
        self.assertEqual(error, "")

    @patch("wintermute.runner.subprocess.run")
    def test_check_vm_memory_available_zero_reserve(self, mock_run: MagicMock) -> None:
        """Should skip check when reserve is 0."""
        from wintermute.runner import check_vm_memory_available, SSHSpec

        spec = SSHSpec(host="localhost", user="test", port=22, options=[])
        vm = VMTargetRecord(
            id="vm1",
            name="Test VM",
            host="localhost",
            user="test",
            port=22,
            required_reserve_memory_gb=0.0, # No reserve
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )
        agent = AgentRecord(
            id="agent1",
            name="Test Agent",
            slug="test-agent",
            command="echo",
            session_mode="cli",
            vm_target_id="vm1",
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            llm_base_url=None,
            llm_api_key=None,
            llm_model=None,
            session_file_config_id=None,
            average_memory_usage_mb=1000,
            initial_prompt=None,
            working_directory=None,
            session_directory=None,
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )

        ok, error = check_vm_memory_available(spec, vm, agent)
        self.assertTrue(ok)
        self.assertEqual(error, "")
        # Should not have called SSH
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
