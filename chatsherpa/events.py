"""Event/error bus + webhook sink.

The host plugs into failures and lifecycle events two ways:

* in-process callbacks via :meth:`EventBus.on` (or ``ChatSherpa.on_event`` /
  ``on_error``);
* a :class:`WebhookSink` that POSTs signed JSON to a URL.

Core stays dependency-light: the webhook uses the stdlib (``urllib``) in a
thread, so no aiohttp/requests needed just to emit errors.
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import hmac
import json
import logging
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

log = logging.getLogger("chatsherpa.events")


class EventType(str, enum.Enum):
    LOGIN_STARTED = "login_started"
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    LOGIN_TIMEOUT = "login_timeout"
    ASK_STARTED = "ask_started"
    ASK_SUCCEEDED = "ask_succeeded"
    ERROR = "error"


class ErrorType(str, enum.Enum):
    LOGIN_REQUIRED = "LoginRequired"
    RESPONSE_TIMEOUT = "ResponseTimeout"
    SELECTOR_MISS = "SelectorMiss"
    RATE_LIMITED = "RateLimited"
    UNKNOWN = "Unknown"


@dataclass
class Event:
    type: EventType
    provider: Optional[str] = None
    user_id: Optional[Union[str, int]] = None
    message: str = ""
    error_type: Optional[ErrorType] = None
    data: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["event"] = self.type.value
        if self.error_type:
            d["error_type"] = self.error_type.value
            d["type"] = self.error_type.value  # webhook consumers key on this
        return d


Listener = Callable[[Event], Union[None, Awaitable[None]]]


class EventBus:
    """Fan-out of events to registered listeners (sync or async)."""

    def __init__(self) -> None:
        self._listeners: List[Listener] = []

    def on(self, callback: Listener) -> Listener:
        self._listeners.append(callback)
        return callback

    async def emit(self, event: Event) -> None:
        for cb in list(self._listeners):
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # a broken listener must not sink the others
                log.exception("event listener failed for %s", event.type)


class WebhookSink:
    """POST events as JSON to ``url``, optionally HMAC-signed, with retries.

    Register it like any listener::

        bus.on(WebhookSink("https://host/ai-errors", secret="..."))

    By default it only forwards ``ERROR`` events (set ``events=None`` for all).
    """

    def __init__(
        self,
        url: str,
        secret: Optional[str] = None,
        events: Optional[List[EventType]] = (EventType.ERROR,),
        timeout: float = 10.0,
        retries: int = 3,
    ) -> None:
        self.url = url
        self.secret = secret.encode() if secret else None
        self.events = set(events) if events else None
        self.timeout = timeout
        self.retries = retries

    async def __call__(self, event: Event) -> None:
        if self.events is not None and event.type not in self.events:
            return
        body = json.dumps(event.to_dict()).encode("utf-8")
        await asyncio.get_event_loop().run_in_executor(None, self._post, body)

    def _post(self, body: bytes) -> None:
        headers = {"Content-Type": "application/json", "User-Agent": "chatsherpa"}
        if self.secret:
            sig = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
            headers["X-ChatSherpa-Signature"] = f"sha256={sig}"
        delay = 0.5
        for attempt in range(1, self.retries + 1):
            try:
                req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
                urllib.request.urlopen(req, timeout=self.timeout).close()
                return
            except Exception as exc:  # noqa: BLE001
                if attempt == self.retries:
                    log.warning("webhook POST failed after %d tries: %s", self.retries, exc)
                    return
                time.sleep(delay)
                delay *= 2
