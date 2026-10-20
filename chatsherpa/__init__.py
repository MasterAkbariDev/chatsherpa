"""chatsherpa: drive AI chat web UIs headlessly with Playwright.

Library quick start::

    import asyncio
    from chatsherpa import ChatSherpa, FileSessionStore

    async def main():
        bridge = ChatSherpa(store=FileSessionStore("sessions"))
        print(await bridge.ask("chatgpt", user_id=42, prompt="Hello!"))

    asyncio.run(main())

Low-level single-provider use is still available via the provider classes.
"""

from __future__ import annotations

from .base import BaseProvider, ProviderConfig
from .client import ChatSherpa
from .events import (
    ErrorType,
    Event,
    EventBus,
    EventType,
    WebhookSink,
)
from .exceptions import (
    BridgeError,
    LoginRequiredError,
    ProviderError,
    RateLimitedError,
    ResponseTimeoutError,
    SessionError,
)
from .providers import (
    REGISTRY,
    ChatGPTProvider,
    DeepSeekProvider,
    GeminiProvider,
    GrokProvider,
    get_provider,
)
from .session import Session
from .store import FileSessionStore, MemorySessionStore, SessionStore

__version__ = "0.1.0"

__all__ = [
    # facade
    "ChatSherpa",
    # storage
    "SessionStore", "FileSessionStore", "MemorySessionStore",
    # events
    "EventBus", "WebhookSink", "Event", "EventType", "ErrorType",
    # providers
    "BaseProvider", "ProviderConfig", "get_provider", "REGISTRY",
    "ChatGPTProvider", "DeepSeekProvider", "GrokProvider", "GeminiProvider",
    # sessions + errors
    "Session",
    "BridgeError", "ProviderError", "LoginRequiredError",
    "ResponseTimeoutError", "RateLimitedError", "SessionError",
    "__version__",
]
