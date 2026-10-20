"""ChatSherpa — the one object a host app holds.

    bridge = ChatSherpa(store=FileSessionStore("sessions"))
    answer = await bridge.ask("chatgpt", user_id=42, prompt="hi")

Owns the session store, browser pool, and event bus. Never leaks Playwright
objects; everything is provider name + user id + text.
"""

from __future__ import annotations

import logging
from typing import Optional, Union

from .base import ProviderConfig
from .events import ErrorType, Event, EventBus, EventType
from .exceptions import LoginRequiredError, RateLimitedError, ResponseTimeoutError
from .login.local import local_login
from .pool import BrowserPool
from .providers import get_provider
from .store import DEFAULT_USER, MemorySessionStore, SessionStore

log = logging.getLogger("chatsherpa.client")

UserId = Union[str, int]


class ChatSherpa:
    def __init__(
        self,
        store: Optional[SessionStore] = None,
        bus: Optional[EventBus] = None,
        pool: Optional[BrowserPool] = None,
        headless: bool = True,
        config: Optional[ProviderConfig] = None,
    ):
        self.store = store or MemorySessionStore()
        self.bus = bus or EventBus()
        self.config = config or ProviderConfig(headless=headless)
        self.pool = pool or BrowserPool(headless=headless, config=self.config)

    # -------- event wiring --------
    def on_event(self, callback):
        return self.bus.on(callback)

    def on_error(self, callback):
        def _filter(event: Event):
            if event.type == EventType.ERROR:
                return callback(event)
        return self.bus.on(_filter)

    def add_sink(self, sink):
        return self.bus.on(sink)

    # -------- core --------
    async def ask(self, provider: str, user_id: UserId, prompt: str,
                  model: Optional[str] = None,
                  model_order: Optional[list] = None) -> str:
        provider_cls = get_provider(provider)
        uid = str(user_id)
        session = await self.store.get(uid, provider_cls.name)
        if session is None or session.is_empty():
            await self._error(provider_cls.name, uid, ErrorType.LOGIN_REQUIRED,
                              "no session stored for this user")
            raise LoginRequiredError(f"[{provider}] no session for user {uid}. Run a login first.")

        await self.bus.emit(Event(EventType.ASK_STARTED, provider=provider_cls.name, user_id=uid))
        lease = await self.pool.lease(provider_cls, session, uid)
        try:
            async with lease as prov:
                text = await prov.send(prompt, model=model, model_order=model_order)
        except LoginRequiredError as exc:
            await self.pool.drop(uid, provider_cls.name)
            await self._error(provider_cls.name, uid, ErrorType.LOGIN_REQUIRED, str(exc))
            raise
        except ResponseTimeoutError as exc:
            await self._error(provider_cls.name, uid, ErrorType.RESPONSE_TIMEOUT, str(exc))
            raise
        except RateLimitedError as exc:
            await self._error(provider_cls.name, uid, ErrorType.RATE_LIMITED, str(exc))
            raise
        except Exception as exc:
            await self._error(provider_cls.name, uid, ErrorType.SELECTOR_MISS, str(exc))
            raise
        await self.bus.emit(Event(EventType.ASK_SUCCEEDED, provider=provider_cls.name, user_id=uid))
        return text

    async def login_local(self, provider: str, user_id: UserId = DEFAULT_USER,
                          email: Optional[str] = None, password: Optional[str] = None,
                          headless: bool = False, timeout_s: int = 300) -> None:
        """Headful login on this machine; stores the captured session."""
        provider_cls = get_provider(provider)
        await local_login(provider_cls, self.store, user_id, email=email, password=password,
                          headless=headless, timeout_s=timeout_s, config=self.config)
        await self.bus.emit(Event(EventType.LOGIN_SUCCEEDED, provider=provider_cls.name,
                                  user_id=str(user_id)))

    async def has_session(self, provider: str, user_id: UserId = DEFAULT_USER) -> bool:
        s = await self.store.get(str(user_id), get_provider(provider).name)
        return bool(s and not s.is_empty())

    async def _error(self, provider, user_id, etype: ErrorType, message: str):
        await self.bus.emit(Event(EventType.ERROR, provider=provider, user_id=user_id,
                                  error_type=etype, message=message))

    async def aclose(self) -> None:
        await self.pool.close()
