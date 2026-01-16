"""Chat platform adapters for multi-platform message dispatch."""

from wintermute.chat.adapters import ChatPlatformAdapter, SlackAdapter
from wintermute.chat.dispatcher import ChatDispatcher

__all__ = ["ChatPlatformAdapter", "SlackAdapter", "ChatDispatcher"]
