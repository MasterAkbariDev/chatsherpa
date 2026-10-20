"""Host-app integrations.

Framework-agnostic glue plus thin adapters that mount onto a host's *existing*
bot/application. The library never owns its own Bot instance.
"""

from __future__ import annotations

from .telegram import TelegramBridge

__all__ = ["TelegramBridge"]
