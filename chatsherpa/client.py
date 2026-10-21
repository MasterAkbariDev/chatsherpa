"""ChatSherpa — the one object a host app holds.

    bridge = ChatSherpa(store=FileSessionStore("sessions"))
    answer = await bridge.ask("chatgpt", user_id=42, prompt="hi")

Owns the session store, browser pool, and event bus. Never leaks Playwright
objects; everything is provider name + user id + text.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Union

from .base import ProviderConfig
from .events import ErrorType, Event, EventBus, EventType
from .exceptions import (
    BotChallengeError,
    BrowserUnavailableError,
    ChatFullError,
    LoginRequiredError,
    RateLimitedError,
    ResponseTimeoutError,
)
from .login.local import local_login
from .pool import BrowserPool
from .providers import get_provider
from .store import DEFAULT_CHAT, DEFAULT_USER, MemorySessionStore, SessionStore

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
                  model_order: Optional[list] = None,
                  new_chat: bool = False,
                  account: Optional[int] = None,
                  on_model=None, chat: str = DEFAULT_CHAT) -> str:
        """Send ``prompt`` into the user's named ``chat``.

        A chat that already exists is continued; a new name starts a fresh
        conversation and is saved under that name. ``new_chat`` resets it first.
        """
        provider_cls = get_provider(provider)
        uid = str(user_id)
        session = await self.store.get(uid, provider_cls.name)
        if session is None or session.is_empty():
            await self._error(provider_cls.name, uid, ErrorType.LOGIN_REQUIRED,
                              "no session stored for this user")
            raise LoginRequiredError(f"[{provider}] no session for user {uid}. Run a login first.")

        if new_chat:
            await self.pool.drop(uid, provider_cls.name)
        thread = None if new_chat else await self.store.get_thread(uid, provider_cls.name, chat)
        if thread is None:
            # A chat with no saved URL is a new conversation. Do not reuse the page
            # that is open on some other chat.
            await self.pool.drop(uid, provider_cls.name)
        if account is not None and hasattr(provider_cls, "account_home"):
            same = thread and provider_cls.account_index(thread) == account
            if not same:
                await self.pool.drop(uid, provider_cls.name)
                thread = provider_cls.account_home(account)
        await self.bus.emit(Event(EventType.ASK_STARTED, provider=provider_cls.name, user_id=uid))
        lease = await self.pool.lease(provider_cls, session, uid, start_url=thread)
        try:
            async with lease as prov:
                text = await prov.send(prompt, model=model, model_order=model_order, on_model=on_model)
                await self._remember_thread(provider_cls, uid, prov.page, chat)
        except LoginRequiredError as exc:
            await self.pool.drop(uid, provider_cls.name)
            await self._error(provider_cls.name, uid, ErrorType.LOGIN_REQUIRED, str(exc))
            raise
        except ResponseTimeoutError as exc:
            await self._error(provider_cls.name, uid, ErrorType.RESPONSE_TIMEOUT, str(exc))
            raise
        except BotChallengeError as exc:
            await self._error(provider_cls.name, uid, ErrorType.BOT_CHALLENGE, str(exc))
            raise
        except BrowserUnavailableError as exc:
            await self._error(provider_cls.name, uid, ErrorType.BROWSER_UNAVAILABLE, str(exc))
            raise
        except ChatFullError as exc:
            await self._error(provider_cls.name, uid, ErrorType.CHAT_FULL, str(exc))
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

    async def _remember_thread(self, provider_cls, user_id: str, page, chat: str = DEFAULT_CHAT) -> None:
        """Keep the chat URL under ``chat`` so the next ask continues it instead of opening a new one."""
        deadline = time.monotonic() + 3
        url = page.url
        while not provider_cls.is_conversation(url) and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            url = page.url
        if provider_cls.is_conversation(url):
            await self.store.put_thread(user_id, provider_cls.name, url, chat)

    async def list_chats(self, provider: str, user_id: UserId = DEFAULT_USER) -> dict:
        """Saved chats for this user and provider, as ``{name: url}``."""
        return await self.store.list_threads(str(user_id), get_provider(provider).name)

    async def list_models(self, provider: str, user_id: UserId = DEFAULT_USER,
                          account: Optional[int] = None) -> dict:
        """Read the model menu from the signed-in account. Does not send a prompt."""
        provider_cls = get_provider(provider)
        uid = str(user_id)
        session = await self.store.get(uid, provider_cls.name)
        if session is None or session.is_empty():
            raise LoginRequiredError(f"[{provider}] no session for user {uid}. Run a login first.")
        used = account
        start = None
        if hasattr(provider_cls, "account_home"):
            if used is None:
                thread = await self.store.get_thread(uid, provider_cls.name)
                if thread:
                    used = provider_cls.account_index(thread)
            if used is not None:
                start = provider_cls.account_home(used)
        lease = await self.pool.lease(provider_cls, session, uid, start_url=start)
        async with lease as prov:
            labels = await prov.list_models()
        return {"models": labels, "account": used}

    async def has_session(self, provider: str, user_id: UserId = DEFAULT_USER) -> bool:
        s = await self.store.get(str(user_id), get_provider(provider).name)
        return bool(s and not s.is_empty())

    async def _error(self, provider, user_id, etype: ErrorType, message: str):
        await self.bus.emit(Event(EventType.ERROR, provider=provider, user_id=user_id,
                                  error_type=etype, message=message))

    async def aclose(self) -> None:
        await self.pool.close()
