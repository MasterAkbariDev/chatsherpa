"""Browser pool: reuse warm Chromium contexts across requests.

One Chromium per pool; one context per (user_id, provider) so conversations
persist and cookies are injected once. A per-context lock serializes sends, and
a global semaphore bounds how many run at once.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Type
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from .base import BaseProvider, ProviderConfig
from .display import VirtualDisplay
from .exceptions import BrowserUnavailableError
from .session import Session

log = logging.getLogger("chatsherpa.pool")


@dataclass
class _Entry:
    context: BrowserContext
    page: Page
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.monotonic)


class _Lease:
    """Async context manager yielding a provider bound to a leased page."""

    def __init__(self, pool: "BrowserPool", key: Tuple[str, str],
                 entry: _Entry, provider_cls: Type[BaseProvider]):
        self._pool = pool
        self._key = key
        self._entry = entry
        self._provider_cls = provider_cls

    async def __aenter__(self) -> BaseProvider:
        await self._pool._sem.acquire()
        await self._entry.lock.acquire()
        self._entry.last_used = time.monotonic()
        return self._provider_cls.attach(self._entry.page, self._pool.config)

    async def __aexit__(self, *exc) -> None:
        self._entry.lock.release()
        self._pool._sem.release()


class BrowserPool:
    def __init__(self, headless: bool = True, max_contexts: int = 2,
                 max_concurrency: int = 6, config: Optional[ProviderConfig] = None,
                 idle_seconds: float = 300.0):
        self.headless = headless
        self.max_contexts = max_contexts
        # A warm page keeps its renderer busy; on a small host, idle ones starve the CPU.
        self.idle_seconds = idle_seconds
        self.config = config or ProviderConfig(headless=headless)
        self.config.headless = headless
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._display = VirtualDisplay()
        self._entries: Dict[Tuple[str, str], _Entry] = {}
        self._sem = asyncio.Semaphore(max_concurrency)
        self._start_lock = asyncio.Lock()
        self._sweeper: Optional[asyncio.Task] = None

    async def start(self) -> None:
        async with self._start_lock:
            if self._browser:
                return
            # Start Xvfb before the Playwright driver. The driver copies the
            # environment when it starts, so DISPLAY set afterwards never
            # reaches Chrome. Headless Chromium is stopped by Cloudflare here.
            launch_headless = self.headless
            # Only a headed run needs a display. Headed Chromium on Xvfb crashed at launch
            # (SIGTRAP) on the VPS, while headless launches were reliable, so headless
            # runs never start Xvfb.
            if not self.headless and self._display.start_if_needed():
                launch_headless = False
            try:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=launch_headless,
                    proxy=self.config.proxy,  # type: ignore[arg-type]
                    args=[*self.config.launch_args, "--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"],
                )
            except Exception as exc:
                if self._pw:
                    await self._pw.stop()
                    self._pw = None
                raise BrowserUnavailableError(
                    f"browser could not start: {exc}. Check free disk space, /tmp, and the "
                    "Chromium install (python -m playwright install chromium)."
                ) from exc
            log.info("browser pool started (headless=%s)", launch_headless)
            # Sweep idle contexts on a timer too: without it, the last browser of a quiet
            # period keeps its renderer running (and the CPU busy) until the next request.
            self._sweeper = asyncio.create_task(self._sweep_forever())

    async def _sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self._drop_idle(keep=None)
            except Exception as exc:  # noqa: BLE001 - keep sweeping
                log.warning("idle sweep failed: %s", exc)

    async def close(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            self._sweeper = None
        for entry in self._entries.values():
            try:
                await entry.context.close()
            except Exception:
                pass
        self._entries.clear()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._display.stop()
        self._pw = self._browser = None

    async def lease(self, provider_cls: Type[BaseProvider], session: Session,
                    user_id: str, start_url: Optional[str] = None) -> _Lease:
        await self.start()
        key = (str(user_id), provider_cls.name)
        entry = self._entries.get(key)
        # A user may have several named chats; the cached page must be on the
        # one asked for, otherwise reopen it.
        if entry is not None and start_url and provider_cls.is_conversation(start_url):
            if urlparse(entry.page.url).path.rstrip("/") != urlparse(start_url).path.rstrip("/"):
                await self.drop(str(user_id), provider_cls.name)
                entry = None
        if entry is None:
            entry = await self._make_entry(provider_cls, session, start_url)
            self._entries[key] = entry
            await self._evict_if_needed()
        await self._drop_idle(keep=key)
        return _Lease(self, key, entry, provider_cls)

    async def _make_entry(self, provider_cls: Type[BaseProvider], session: Session,
                          start_url: Optional[str] = None) -> _Entry:
        assert self._browser is not None
        ctx_kwargs: dict = {}
        if self.config.user_agent:
            ctx_kwargs["user_agent"] = self.config.user_agent
        else:
            # Headless Chromium's default user agent says "HeadlessChrome", which the
            # providers' bot checks reject. Send the same string as desktop Chrome.
            ctx_kwargs["user_agent"] = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{self._browser.version} Safari/537.36"
            )
        if self.config.locale:
            ctx_kwargs["locale"] = self.config.locale
        ctx_kwargs["viewport"] = self.config.viewport or {"width": 1280, "height": 800}
        storage = session.storage_state_arg()
        if storage is not None:
            ctx_kwargs["storage_state"] = storage
        context = await self._browser.new_context(**ctx_kwargs)
        context.set_default_timeout(self.config.nav_timeout_ms)
        cookies = session.cookies_for(provider_cls.home_url)
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()
        target = start_url if start_url and provider_cls.entry_url(start_url) else provider_cls.home_url
        try:
            await page.goto(target, wait_until="domcontentloaded",
                            timeout=self.config.nav_timeout_ms)
        except Exception:
            if target == provider_cls.home_url:
                raise
            log.info("saved chat %s did not open; starting a new one", target)
            await page.goto(provider_cls.home_url, wait_until="domcontentloaded",
                            timeout=self.config.nav_timeout_ms)
        await provider_cls.attach(page, self.config)._after_load()
        return _Entry(context, page)

    async def _drop_idle(self, keep=None) -> None:
        """Close contexts nobody has used for ``idle_seconds`` (never the one in use)."""
        now = time.monotonic()
        for key, entry in list(self._entries.items()):
            if key == keep or entry.lock.locked():
                continue
            if now - entry.last_used > self.idle_seconds:
                log.info("closing idle context %s", key)
                await self.drop(key[0], key[1])

    async def _evict_if_needed(self) -> None:
        while len(self._entries) > self.max_contexts:
            oldest = min(self._entries.items(), key=lambda kv: kv[1].last_used)
            key, entry = oldest
            if entry.lock.locked():
                break
            try:
                await entry.context.close()
            except Exception:
                pass
            self._entries.pop(key, None)
            log.info("evicted idle context %s", key)

    async def drop(self, user_id: str, provider: str) -> None:
        """Discard a context (e.g. after an expired-session error)."""
        key = (str(user_id), provider)
        entry = self._entries.pop(key, None)
        if entry:
            try:
                await entry.context.close()
            except Exception:
                pass
