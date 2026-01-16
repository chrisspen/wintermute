"""Chat platform adapters for multi-platform message dispatch.

This module provides an abstract base class for chat platform adapters and
concrete implementations for supported platforms (Slack, Telegram, Discord, etc.).

Each adapter handles platform-specific message formatting and API calls while
exposing a common interface for the dispatcher.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional
import logging


@dataclass
class MessageResult:
    """Result of sending a message."""

    success: bool
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    error: Optional[str] = None


class ChatPlatformAdapter(ABC):
    """Abstract base class for chat platform adapters.

    Each platform adapter implements send_message and other platform-specific
    operations while exposing a common interface for the dispatcher.
    """

    @property
    @abstractmethod
    def platform_type(self) -> str:
        """Return the platform identifier (e.g., 'slack', 'telegram', 'discord')."""
        ...

    @abstractmethod
    async def send_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: Optional[str] = None,
    ) -> MessageResult:
        """Send a message to a channel.

        Args:
            channel_id: Platform-specific channel identifier.
            text: Message text to send.
            thread_ts: Optional thread identifier for threaded replies.

        Returns:
            MessageResult with success status and any platform-specific IDs.
        """
        ...

    async def send_thread_reply(
        self,
        channel_id: str,
        thread_ts: str,
        text: str,
    ) -> MessageResult:
        """Send a reply in a thread.

        Default implementation calls send_message with thread_ts.
        Subclasses may override for platform-specific threading behavior.
        """
        return await self.send_message(channel_id, text, thread_ts=thread_ts)


class SlackAdapter(ChatPlatformAdapter):
    """Slack chat platform adapter using the Slack SDK."""

    def __init__(self, bot_token: str) -> None:
        """Initialize the Slack adapter.

        Args:
            bot_token: Slack bot OAuth token.
        """
        from slack_sdk.web.async_client import AsyncWebClient

        self._token = bot_token
        self._client = AsyncWebClient(token=bot_token)
        self._logger = logging.getLogger(__name__)

    @property
    def platform_type(self) -> str:
        return "slack"

    async def send_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: Optional[str] = None,
    ) -> MessageResult:
        """Send a message to a Slack channel.

        Args:
            channel_id: Slack channel ID (e.g., 'C12345').
            text: Message text to send.
            thread_ts: Optional thread timestamp for replies.

        Returns:
            MessageResult with Slack-specific ts (message ID) and thread_ts.
        """
        try:
            response = await self._client.chat_postMessage(
                channel=channel_id,
                text=text,
                thread_ts=thread_ts,
            )
            if response.get("ok"):
                return MessageResult(
                    success=True,
                    message_id=response.get("ts"),
                    thread_id=response.get("thread_ts") or thread_ts,
                )
            return MessageResult(
                success=False,
                error=response.get("error", "Unknown Slack error"),
            )
        except Exception as exc:
            self._logger.warning("Slack send_message failed: %s", exc)
            return MessageResult(success=False, error=str(exc))


class TelegramAdapter(ChatPlatformAdapter):
    """Telegram chat platform adapter (placeholder for future implementation)."""

    def __init__(self, bot_token: str) -> None:
        self._token = bot_token
        self._logger = logging.getLogger(__name__)

    @property
    def platform_type(self) -> str:
        return "telegram"

    async def send_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: Optional[str] = None,
    ) -> MessageResult:
        """Send a message to a Telegram chat.

        Note: Not yet implemented. Returns error result.
        """
        self._logger.warning("Telegram adapter not yet implemented")
        return MessageResult(success=False, error="Telegram adapter not implemented")


class DiscordAdapter(ChatPlatformAdapter):
    """Discord chat platform adapter (placeholder for future implementation)."""

    def __init__(self, bot_token: str) -> None:
        self._token = bot_token
        self._logger = logging.getLogger(__name__)

    @property
    def platform_type(self) -> str:
        return "discord"

    async def send_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: Optional[str] = None,
    ) -> MessageResult:
        """Send a message to a Discord channel.

        Note: Not yet implemented. Returns error result.
        """
        self._logger.warning("Discord adapter not yet implemented")
        return MessageResult(success=False, error="Discord adapter not implemented")
