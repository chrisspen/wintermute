"""Chat dispatcher for routing messages to platform adapters.

This module provides a unified interface for sending messages across
multiple chat platforms based on channel configuration.
"""

from __future__ import annotations

from typing import Any, Optional
import logging

from wintermute.chat.adapters import (
    ChatPlatformAdapter,
    DiscordAdapter,
    MessageResult,
    SlackAdapter,
    TelegramAdapter,
)
from wintermute.db import ChannelRecord, Database


class ChatDispatcher:
    """Dispatcher that routes messages to appropriate platform adapters.

    The dispatcher maintains a registry of platform adapters and routes
    messages based on channel type. It handles adapter initialization
    and caching.
    """

    # Platform type to adapter class mapping
    ADAPTER_CLASSES = {
        "slack": SlackAdapter,
        "telegram": TelegramAdapter,
        "discord": DiscordAdapter,
    }

    def __init__(self, db: Database) -> None:
        """Initialize the dispatcher.

        Args:
            db: Database instance for fetching credentials.
        """
        self._db = db
        self._adapters: dict[str, ChatPlatformAdapter] = {}
        self._logger = logging.getLogger(__name__)

    def _get_adapter(self, platform_type: str) -> Optional[ChatPlatformAdapter]:
        """Get or create an adapter for the given platform type.

        Args:
            platform_type: Platform identifier (e.g., 'slack').

        Returns:
            Adapter instance or None if unavailable.
        """
        if platform_type in self._adapters:
            self._logger.debug("Using cached adapter for %s", platform_type)
            return self._adapters[platform_type]

        adapter_class = self.ADAPTER_CLASSES.get(platform_type)
        if not adapter_class:
            self._logger.warning("Unknown platform type: %s", platform_type)
            return None

        # Get platform-specific credentials
        token = self._get_platform_token(platform_type)
        if not token:
            self._logger.warning(
                "No token found for platform: %s (provider=%s, name=bot_token)",
                platform_type,
                platform_type,
            )
            return None

        try:
            self._logger.info("Creating %s adapter", platform_type)
            adapter = adapter_class(bot_token=token)
            self._adapters[platform_type] = adapter
            return adapter
        except Exception as exc:
            self._logger.error("Failed to create %s adapter: %s", platform_type, exc)
            return None

    def _get_platform_token(self, platform_type: str) -> Optional[str]:
        """Get the bot token for a platform.

        Args:
            platform_type: Platform identifier.

        Returns:
            Token string or None.
        """
        # Platform-specific token names
        token_names = {
            "slack": "bot_token",
            "telegram": "bot_token",
            "discord": "bot_token",
        }
        token_name = token_names.get(platform_type, "bot_token")

        cred = self._db.get_credential_by_name(platform_type, token_name)
        return cred.reference if cred else None

    async def send_to_channel(
        self,
        channel: ChannelRecord,
        text: str,
        *,
        thread_ts: Optional[str] = None,
    ) -> MessageResult:
        """Send a message to a channel.

        Args:
            channel: ChannelRecord with platform type and external ID.
            text: Message text to send.
            thread_ts: Optional thread identifier for replies.

        Returns:
            MessageResult with send status.
        """
        if not channel.enabled:
            return MessageResult(success=False, error="Channel disabled")

        if not channel.external_channel_id:
            return MessageResult(success=False, error="No external channel ID")

        adapter = self._get_adapter(channel.type)
        if not adapter:
            return MessageResult(
                success=False,
                error=f"No adapter for platform: {channel.type}",
            )

        return await adapter.send_message(
            channel.external_channel_id,
            text,
            thread_ts=thread_ts,
        )

    async def broadcast_to_agent_channels(
        self,
        agent_id: str,
        text: str,
        *,
        platform_filter: Optional[str] = None,
    ) -> list[tuple[ChannelRecord, MessageResult]]:
        """Broadcast a message to all channels for an agent.

        Args:
            agent_id: Agent ID to send to.
            text: Message text to send.
            platform_filter: Optional platform type to filter channels.

        Returns:
            List of (channel, result) tuples.
        """
        channels = self._db.list_channels(agent_id=agent_id)
        self._logger.info("Broadcasting to %d channels for agent %s", len(channels), agent_id)
        results: list[tuple[ChannelRecord, MessageResult]] = []

        for channel in channels:
            if not channel.enabled:
                self._logger.debug("Skipping disabled channel %s", channel.name)
                continue
            if platform_filter and channel.type != platform_filter:
                continue

            if not channel.external_channel_id:
                self._logger.warning(
                    "Channel %s has no external_channel_id configured - skipping",
                    channel.name,
                )
                continue
            self._logger.info(
                "Sending to channel %s (%s: %s)",
                channel.name,
                channel.type,
                channel.external_channel_id,
            )
            result = await self.send_to_channel(channel, text)
            results.append((channel, result))
            if result.success:
                self._logger.info("Successfully sent to channel %s", channel.name)
            else:
                self._logger.warning(
                    "Failed to send to channel %s: %s",
                    channel.name,
                    result.error,
                )

        return results

    async def send_to_project_slack(
        self,
        project: Any,
        text: str,
        *,
        thread_ts: Optional[str] = None,
    ) -> MessageResult:
        """Send a message to a project's Slack channel.

        This is a convenience method for project-based Slack dispatch,
        maintaining backward compatibility with the existing pattern.

        Args:
            project: Project record with slack_channel_id.
            text: Message text to send.
            thread_ts: Optional thread timestamp for replies.

        Returns:
            MessageResult with send status.
        """
        if not project or not project.slack_channel_id:
            return MessageResult(success=False, error="No Slack channel configured")

        adapter = self._get_adapter("slack")
        if not adapter:
            return MessageResult(success=False, error="Slack adapter unavailable")

        return await adapter.send_message(
            project.slack_channel_id,
            text,
            thread_ts=thread_ts,
        )
