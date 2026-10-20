"""Login broker + handle shared by local and remote flows."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Type

from ..base import BaseProvider


@dataclass
class LoginHandle:
    """A pending login the caller can await."""

    token: str
    provider: str
    user_id: str
    mode: str = "remote"
    url: Optional[str] = None
    status: str = "pending"  # pending | success | failed | timeout
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())

    async def wait(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Block until the session is caught; returns the storageState dict."""
        return await asyncio.wait_for(asyncio.shield(self.future), timeout)


@dataclass
class PendingLogin:
    token: str
    provider_cls: Type[BaseProvider]
    user_id: str
    handle: LoginHandle
    created: float = field(default_factory=time.monotonic)
    ttl: float = 600.0
    # populated by the remote server once a browser context exists:
    context: Any = None
    page: Any = None
    catcher: Any = None

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > self.ttl


class LoginBroker:
    """Tracks in-flight logins by one-time token."""

    def __init__(self) -> None:
        self._pending: Dict[str, PendingLogin] = {}

    def create(self, provider_cls: Type[BaseProvider], user_id: str, ttl: float = 600.0) -> PendingLogin:
        token = secrets.token_urlsafe(24)
        handle = LoginHandle(token=token, provider=provider_cls.name, user_id=str(user_id))
        pending = PendingLogin(token, provider_cls, str(user_id), handle, ttl=ttl)
        self._pending[token] = pending
        return pending

    def get(self, token: str) -> Optional[PendingLogin]:
        p = self._pending.get(token)
        if p and p.expired:
            self.reject(token, "timeout")
            return None
        return p

    def resolve(self, token: str, storage_state: Dict[str, Any]) -> None:
        p = self._pending.pop(token, None)
        if p and not p.handle.future.done():
            p.handle.status = "success"
            p.handle.future.set_result(storage_state)

    def reject(self, token: str, reason: str) -> None:
        p = self._pending.pop(token, None)
        if p and not p.handle.future.done():
            p.handle.status = reason
            p.handle.future.set_exception(TimeoutError(f"login {reason}"))

    def active(self) -> int:
        return len(self._pending)
