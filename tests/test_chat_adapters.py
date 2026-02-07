"""Tests for chat platform adapters and dispatcher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asgiref.sync import sync_to_async

from wintermute.chat.adapters import (
    DiscordAdapter,
    MessageResult,
    SlackAdapter,
    TelegramAdapter,
)
from wintermute.chat.dispatcher import ChatDispatcher
from wintermute.models import Agent, Channel, Credential
from wintermute.models import Channel as ChannelRecord
from wintermute.services.database import AsyncDatabase, Database


class TestSlackAdapter:
    """Tests for SlackAdapter."""

    @pytest.mark.asyncio
    async def test_platform_type_is_slack(self) -> None:
        """SlackAdapter should return 'slack' as platform type."""
        with patch("slack_sdk.web.async_client.AsyncWebClient"):
            adapter = SlackAdapter(bot_token="xoxb-test-token")
            assert adapter.platform_type == "slack"

    @pytest.mark.asyncio
    async def test_send_message_success(self) -> None:
        """SlackAdapter should return success with message ID on success."""
        with patch("slack_sdk.web.async_client.AsyncWebClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "1234567890.123456", "channel": "C123"})
            mock_client_cls.return_value = mock_client

            adapter = SlackAdapter(bot_token="xoxb-test-token")
            result = await adapter.send_message("C123", "Hello, world!")

            assert result.success
            assert result.message_id == "1234567890.123456"
            mock_client.chat_postMessage.assert_called_once_with(
                channel="C123",
                text="Hello, world!",
                thread_ts=None,
            )

    @pytest.mark.asyncio
    async def test_send_message_with_thread(self) -> None:
        """SlackAdapter should pass thread_ts to API."""
        with patch("slack_sdk.web.async_client.AsyncWebClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(return_value={
                "ok": True,
                "ts": "1234567890.123456",
                "thread_ts": "1234567890.000000",
            })
            mock_client_cls.return_value = mock_client

            adapter = SlackAdapter(bot_token="xoxb-test-token")
            result = await adapter.send_message("C123", "Reply", thread_ts="1234567890.000000")

            assert result.success
            assert result.thread_id == "1234567890.000000"
            mock_client.chat_postMessage.assert_called_once_with(
                channel="C123",
                text="Reply",
                thread_ts="1234567890.000000",
            )

    @pytest.mark.asyncio
    async def test_send_message_api_error(self) -> None:
        """SlackAdapter should return error on API failure."""
        with patch("slack_sdk.web.async_client.AsyncWebClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(return_value={"ok": False, "error": "channel_not_found"})
            mock_client_cls.return_value = mock_client

            adapter = SlackAdapter(bot_token="xoxb-test-token")
            result = await adapter.send_message("C123", "Hello")

            assert not result.success
            assert result.error == "channel_not_found"

    @pytest.mark.asyncio
    async def test_send_message_exception(self) -> None:
        """SlackAdapter should handle exceptions gracefully."""
        with patch("slack_sdk.web.async_client.AsyncWebClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(side_effect=Exception("Network error"))
            mock_client_cls.return_value = mock_client

            adapter = SlackAdapter(bot_token="xoxb-test-token")
            result = await adapter.send_message("C123", "Hello")

            assert not result.success
            assert "Network error" in result.error

    @pytest.mark.asyncio
    async def test_send_thread_reply_uses_send_message(self) -> None:
        """SlackAdapter.send_thread_reply should delegate to send_message."""
        with patch("slack_sdk.web.async_client.AsyncWebClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "1234567890.123456"})
            mock_client_cls.return_value = mock_client

            adapter = SlackAdapter(bot_token="xoxb-test-token")
            result = await adapter.send_thread_reply("C123", "1234567890.000000", "Reply text")

            assert result.success
            mock_client.chat_postMessage.assert_called_once_with(
                channel="C123",
                text="Reply text",
                thread_ts="1234567890.000000",
            )


class TestTelegramAdapter:
    """Tests for TelegramAdapter (placeholder)."""

    @pytest.mark.asyncio
    async def test_platform_type_is_telegram(self) -> None:
        """TelegramAdapter should return 'telegram' as platform type."""
        adapter = TelegramAdapter(bot_token="test-token")
        assert adapter.platform_type == "telegram"

    @pytest.mark.asyncio
    async def test_send_message_returns_not_implemented(self) -> None:
        """TelegramAdapter should return not implemented error."""
        adapter = TelegramAdapter(bot_token="test-token")
        result = await adapter.send_message("123456", "Hello")

        assert not result.success
        assert "not implemented" in result.error.lower()


class TestDiscordAdapter:
    """Tests for DiscordAdapter (placeholder)."""

    @pytest.mark.asyncio
    async def test_platform_type_is_discord(self) -> None:
        """DiscordAdapter should return 'discord' as platform type."""
        adapter = DiscordAdapter(bot_token="test-token")
        assert adapter.platform_type == "discord"

    @pytest.mark.asyncio
    async def test_send_message_returns_not_implemented(self) -> None:
        """DiscordAdapter should return not implemented error."""
        adapter = DiscordAdapter(bot_token="test-token")
        result = await adapter.send_message("123456", "Hello")

        assert not result.success
        assert "not implemented" in result.error.lower()


@pytest.mark.django_db(transaction=True)
class TestChatDispatcher:
    """Tests for ChatDispatcher."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.db = Database(":memory:") # Path ignored, uses Django's test DB
        # Create an agent for channel tests
        self.agent = Agent.objects.create(
            id="agent-1",
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
        )

    def teardown_method(self) -> None:
        """Clean up test data."""
        Channel.objects.all().delete()
        Credential.objects.all().delete()
        Agent.objects.all().delete()

    @pytest.mark.asyncio
    async def test_send_to_channel_disabled_channel(self) -> None:
        """Dispatcher should return error for disabled channel."""
        dispatcher = ChatDispatcher(self.db)
        channel = ChannelRecord(
            id="ch-1",
            agent_id="agent-1",
            type="slack",
            name="test-channel",
            external_channel_id="C123",
            enabled=False,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        result = await dispatcher.send_to_channel(channel, "Hello")

        assert not result.success
        assert "disabled" in result.error.lower()

    @pytest.mark.asyncio
    async def test_send_to_channel_no_external_id(self) -> None:
        """Dispatcher should return error for channel without external ID."""
        dispatcher = ChatDispatcher(self.db)
        channel = ChannelRecord(
            id="ch-1",
            agent_id="agent-1",
            type="slack",
            name="test-channel",
            external_channel_id="",
            enabled=True,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        result = await dispatcher.send_to_channel(channel, "Hello")

        assert not result.success
        assert "external" in result.error.lower()

    @pytest.mark.asyncio
    async def test_send_to_channel_unknown_platform(self) -> None:
        """Dispatcher should return error for unknown platform type."""
        dispatcher = ChatDispatcher(self.db)
        channel = ChannelRecord(
            id="ch-1",
            agent_id="agent-1",
            type="unknown_platform",
            name="test-channel",
            external_channel_id="123",
            enabled=True,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        result = await dispatcher.send_to_channel(channel, "Hello")

        assert not result.success
        assert "adapter" in result.error.lower()

    @pytest.mark.asyncio
    async def test_send_to_channel_no_token(self) -> None:
        """Dispatcher should return error when platform token is not configured."""
        dispatcher = ChatDispatcher(self.db)
        channel = ChannelRecord(
            id="ch-1",
            agent_id="agent-1",
            type="slack",
            name="test-channel",
            external_channel_id="C123",
            enabled=True,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        result = await dispatcher.send_to_channel(channel, "Hello")

        assert not result.success
        # Either "adapter" or "token" depending on error path
        assert "adapter" in result.error.lower() or "token" in result.error.lower()

    @pytest.mark.asyncio
    async def test_send_to_channel_success(self) -> None:
        """Dispatcher should send message via adapter when configured."""
        # Add Slack credential
        await sync_to_async(Credential.objects.create)(
            id="cred-1",
            provider="slack",
            name="bot_token",
            reference="xoxb-test-token",
        )

        channel = ChannelRecord(
            id="ch-1",
            agent_id="agent-1",
            type="slack",
            name="test-channel",
            external_channel_id="C123",
            enabled=True,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        with patch("slack_sdk.web.async_client.AsyncWebClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "1234567890.123456"})
            mock_client_cls.return_value = mock_client

            dispatcher = ChatDispatcher(self.db)
            result = await dispatcher.send_to_channel(channel, "Hello")

            assert result.success
            mock_client.chat_postMessage.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_to_agent_channels(self) -> None:
        """Dispatcher should broadcast to all enabled agent channels."""
        # Add Slack credential
        await sync_to_async(Credential.objects.create)(
            id="cred-1",
            provider="slack",
            name="bot_token",
            reference="xoxb-test-token",
        )

        # Add channels
        await sync_to_async(Channel.objects.create)(
            id="ch-1",
            agent_id=self.agent.id,
            type="slack",
            name="channel-1",
            external_channel_id="C123",
            enabled=True,
        )
        await sync_to_async(Channel.objects.create)(
            id="ch-2",
            agent_id=self.agent.id,
            type="slack",
            name="channel-2",
            external_channel_id="C456",
            enabled=True,
        )
        await sync_to_async(Channel.objects.create)(
            id="ch-3",
            agent_id=self.agent.id,
            type="slack",
            name="disabled-channel",
            external_channel_id="C789",
            enabled=False,
        )

        with patch("slack_sdk.web.async_client.AsyncWebClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "1234567890.123456"})
            mock_client_cls.return_value = mock_client

            dispatcher = ChatDispatcher(self.db)
            results = await dispatcher.broadcast_to_agent_channels("agent-1", "Hello, world!")

            # Should only send to enabled channels (2)
            assert len(results) == 2
            for channel, result in results:
                assert result.success

    @pytest.mark.asyncio
    async def test_broadcast_with_platform_filter(self) -> None:
        """Dispatcher should filter by platform when specified."""
        # Add credentials
        await sync_to_async(Credential.objects.create)(
            id="cred-1",
            provider="slack",
            name="bot_token",
            reference="xoxb-test-token",
        )

        # Add mixed platform channels
        await sync_to_async(Channel.objects.create)(
            id="ch-1",
            agent_id=self.agent.id,
            type="slack",
            name="slack-channel",
            external_channel_id="C123",
            enabled=True,
        )
        await sync_to_async(Channel.objects.create)(
            id="ch-2",
            agent_id=self.agent.id,
            type="telegram",
            name="telegram-channel",
            external_channel_id="123456",
            enabled=True,
        )

        with patch("slack_sdk.web.async_client.AsyncWebClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "1234567890.123456"})
            mock_client_cls.return_value = mock_client

            dispatcher = ChatDispatcher(self.db)
            results = await dispatcher.broadcast_to_agent_channels("agent-1", "Hello!", platform_filter="slack")

            # Should only send to Slack channels (1)
            assert len(results) == 1
            assert results[0][0].type == "slack"

    @pytest.mark.asyncio
    async def test_broadcast_with_async_database(self) -> None:
        """Dispatcher should work with AsyncDatabase (list_channels returns coroutine)."""
        # Create AsyncDatabase wrapper
        async_db = AsyncDatabase(":memory:")

        # Add Slack credential
        await sync_to_async(Credential.objects.create)(
            id="cred-async",
            provider="slack",
            name="bot_token",
            reference="xoxb-test-token",
        )

        # Add channel
        await sync_to_async(Channel.objects.create)(
            id="ch-async",
            agent_id=self.agent.id,
            type="slack",
            name="async-channel",
            external_channel_id="C123",
            enabled=True,
        )

        with patch("slack_sdk.web.async_client.AsyncWebClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "1234567890.123456"})
            mock_client_cls.return_value = mock_client

            # Use AsyncDatabase with dispatcher
            dispatcher = ChatDispatcher(async_db)
            results = await dispatcher.broadcast_to_agent_channels("agent-1", "Hello from async!")

            # Should handle coroutine result from list_channels
            assert len(results) == 1
            assert results[0][1].success


class TestMessageResult:
    """Tests for MessageResult dataclass."""

    def test_success_result(self) -> None:
        """MessageResult should store success state."""
        result = MessageResult(success=True, message_id="123", thread_id="456")
        assert result.success
        assert result.message_id == "123"
        assert result.thread_id == "456"
        assert result.error is None

    def test_error_result(self) -> None:
        """MessageResult should store error state."""
        result = MessageResult(success=False, error="Something went wrong")
        assert not result.success
        assert result.message_id is None
        assert result.error == "Something went wrong"


if __name__ == "__main__":
    pytest.main([__file__])
