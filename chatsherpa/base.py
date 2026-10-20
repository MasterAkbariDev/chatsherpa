"""Base provider: browser lifecycle, session injection, send/receive loop.

Concrete providers (see :mod:`chatsherpa.providers`) subclass
:class:`BaseProvider` and mostly just declare CSS selectors. The heavy lifting
— launching Chromium, applying the session, typing the prompt, and waiting for
a *stable* streamed response — lives here.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PWTimeoutError,
    async_playwright,
)

from .exceptions import LoginRequiredError, RateLimitedError, ResponseTimeoutError
from .session import Session, SessionSource

log = logging.getLogger("chatsherpa")


@dataclass
class ProviderConfig:
    """Runtime knobs shared by every provider."""

    headless: bool = True
    slow_mo: int = 0
    user_agent: Optional[str] = None
    viewport: Optional[Dict[str, int]] = None
    locale: Optional[str] = "en-US"
    proxy: Optional[Dict[str, str]] = None

    nav_timeout_ms: int = 60_000
    response_timeout_ms: int = 180_000
    login_wait_ms: int = 8_000

    # A response is "done" once its text stops changing for this long.
    stability_window_ms: int = 1_800
    poll_interval_ms: int = 350

    # Extra args passed to chromium.launch (e.g. ["--no-sandbox"]).
    launch_args: List[str] = field(default_factory=list)

    # Model selection: a single preferred model, or an ordered preference list
    # (the first one that can be selected wins). Names match the provider's UI
    # labels, e.g. "Expert"/"Fast" (Grok), "2.5 Pro"/"Flash" (Gemini).
    model: Optional[str] = None
    model_order: Optional[List[str]] = None


_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class BaseProvider:
    """Abstract driver for a single AI chat web UI.

    Subclasses MUST set: ``name``, ``home_url``, ``input_selector``,
    ``assistant_message_selector``. Everything else has a sensible default.
    """

    name: str = "base"
    home_url: str = ""

    #: Selector for the message composer. May be a textarea or contenteditable.
    input_selector: str = ""
    #: Whether the composer is a contenteditable element (vs <textarea>/<input>).
    input_is_contenteditable: bool = False
    #: Optional explicit send button. If None/absent, Enter is pressed.
    send_button_selector: Optional[str] = None
    #: Selector matching EVERY assistant message block (newest is ``.last``).
    assistant_message_selector: str = ""
    #: If set, presence of this selector means "generation in progress".
    streaming_indicator_selector: Optional[str] = None
    #: Selectors whose presence means a login/signup wall (not authenticated).
    login_wall_selectors: List[str] = []

    # --- Experimental auto-login form selectors (override per provider) ---
    #: Optional button/link that opens the login form from the home page.
    login_trigger_selector: Optional[str] = None
    email_input_selector: Optional[str] = None
    #: Button to advance past the email step (else Enter is pressed).
    email_submit_selector: Optional[str] = None
    password_input_selector: Optional[str] = None
    #: Button to submit the password (else Enter is pressed).
    password_submit_selector: Optional[str] = None

    def __init__(
        self,
        session: SessionSource = None,
        config: Optional[ProviderConfig] = None,
        selectors: Optional[Dict[str, str]] = None,
    ):
        self.session = Session.coerce(session)
        self.config = config or ProviderConfig()
        # Per-instance selector overrides without subclassing (UIs drift a lot).
        if selectors:
            for key, val in selectors.items():
                setattr(self, key, val)

        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    @classmethod
    def attach(cls, page: Page, config: Optional[ProviderConfig] = None,
               selectors: Optional[Dict[str, str]] = None) -> "BaseProvider":
        """Bind a provider to an already-open page (used by the browser pool).

        Skips ``start()``: the caller owns the browser/context lifecycle.
        """
        inst = cls(config=config, selectors=selectors)
        inst._page = page
        inst._context = page.context
        return inst

    async def __aenter__(self) -> "BaseProvider":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Provider not started. Use `async with` or `await start()`.")
        return self._page

    async def start(self) -> "BaseProvider":
        log.info("[%s] launching chromium (headless=%s)", self.name, self.config.headless)
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.config.headless,
            slow_mo=self.config.slow_mo,
            args=self.config.launch_args,
            proxy=self.config.proxy,  # type: ignore[arg-type]
        )

        ctx_kwargs: Dict[str, object] = {
            "user_agent": self.config.user_agent or _DEFAULT_UA,
            "locale": self.config.locale,
        }
        if self.config.viewport:
            ctx_kwargs["viewport"] = self.config.viewport
        storage = self.session.storage_state_arg()
        if storage is not None:
            ctx_kwargs["storage_state"] = storage

        self._context = await self._browser.new_context(**ctx_kwargs)
        self._context.set_default_timeout(self.config.nav_timeout_ms)

        cookies = self.session.cookies_for(self.home_url)
        if cookies:
            log.info("[%s] injecting %d raw cookie(s)", self.name, len(cookies))
            await self._context.add_cookies(cookies)

        self._page = await self._context.new_page()
        log.info("[%s] navigating to %s", self.name, self.home_url)
        await self._page.goto(
            self.home_url, wait_until="domcontentloaded", timeout=self.config.nav_timeout_ms
        )
        await self._after_load()
        return self

    async def close(self) -> None:
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
        ):
            if closer:
                try:
                    await closer()
                except Exception:  # pragma: no cover - best effort teardown
                    pass
        if self._pw:
            await self._pw.stop()
        self._pw = self._browser = self._context = self._page = None

    async def _after_load(self) -> None:
        """Hook for dismissing cookie banners / intro modals. Override as needed."""
        return None

    # ------------------------------------------------------------------ #
    # Auth
    # ------------------------------------------------------------------ #
    async def is_logged_in(self) -> bool:
        """Best-effort auth check: composer present AND no login wall visible."""
        page = self.page
        for sel in self.login_wall_selectors:
            if sel and await page.locator(sel).count() > 0:
                try:
                    if await page.locator(sel).first.is_visible():
                        return False
                except Exception:
                    pass
        if not self.input_selector:
            return True
        try:
            await page.wait_for_selector(
                self.input_selector, timeout=self.config.login_wait_ms, state="visible"
            )
            return True
        except PWTimeoutError:
            return False

    async def ensure_logged_in(self) -> None:
        if not await self.is_logged_in():
            raise LoginRequiredError(
                f"[{self.name}] not authenticated at {self.page.url}. "
                "Capture/refresh the session (storageState or cookies)."
            )

    async def auto_login(self, email: str, password: str) -> None:
        """EXPERIMENTAL form-based login using the provider's field selectors.

        Fragile by nature: these sites use Cloudflare, Google risk checks,
        CAPTCHAs and 2FA that frequently defeat scripted logins and can lock
        accounts. Prefer :meth:`capture_session` (headful, you type the
        password). This drives: (optional) trigger -> email -> password ->
        submit, and verifies via :meth:`is_logged_in`.
        """
        if not self.email_input_selector or not self.password_input_selector:
            raise NotImplementedError(
                f"[{self.name}] auto_login not configured. Use capture_session()."
            )
        page = self.page

        if self.login_trigger_selector:
            try:
                await page.locator(self.login_trigger_selector).first.click(timeout=8_000)
            except Exception:
                pass  # form may already be visible

        # Email step.
        email_field = page.locator(self.email_input_selector).first
        await email_field.wait_for(state="visible", timeout=20_000)
        await email_field.fill(email)

        # Advance to the password step only if it isn't already on screen
        # (some providers show email+password on one form).
        pw_field = page.locator(self.password_input_selector).first
        if await pw_field.count() == 0 or not await pw_field.is_visible():
            if self.email_submit_selector:
                try:
                    await page.locator(self.email_submit_selector).first.click(timeout=8_000)
                except Exception:
                    await email_field.press("Enter")
            else:
                await email_field.press("Enter")
            await pw_field.wait_for(state="visible", timeout=20_000)

        # Password step.
        await pw_field.fill(password)
        if self.password_submit_selector:
            try:
                await page.locator(self.password_submit_selector).first.click(timeout=8_000)
            except Exception:
                await pw_field.press("Enter")
        else:
            await pw_field.press("Enter")

        # Confirm.
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if await self.is_logged_in():
                log.info("[%s] auto_login succeeded", self.name)
                return
            await asyncio.sleep(2)
        raise LoginRequiredError(
            f"[{self.name}] auto_login did not complete (likely CAPTCHA/2FA). "
            "Finish in the open window or use headful capture."
        )

    @classmethod
    async def capture_session(
        cls,
        out_path: str,
        email: Optional[str] = None,
        password: Optional[str] = None,
        headless: bool = False,
        timeout_s: int = 300,
        poll_s: float = 2.0,
        config: Optional[ProviderConfig] = None,
    ) -> str:
        """Session catcher: log in (auto and/or manually), then save the session.

        If ``email``/``password`` are given, an auto-login attempt is made first;
        then the method polls :meth:`is_logged_in` until you've finished (handy
        when the auto step stops at a CAPTCHA/2FA and you complete it by hand in
        the open window). Writes a Playwright ``storageState`` JSON.
        """
        cfg = config or ProviderConfig()
        cfg.headless = headless
        provider = cls(config=cfg)
        await provider.start()
        try:
            if email and password and not await provider.is_logged_in():
                try:
                    await provider.auto_login(email, password)
                except Exception as exc:
                    log.warning("[%s] auto_login failed: %s", cls.name, exc)
                    if headless:
                        raise
                    log.info("[%s] finish logging in manually in the window...", cls.name)

            log.info("[%s] waiting up to %ss for a logged-in session...", cls.name, timeout_s)
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if await provider.is_logged_in():
                    await provider._context.storage_state(path=out_path)  # type: ignore[union-attr]
                    log.info("[%s] session captured -> %s", cls.name, out_path)
                    return out_path
                await asyncio.sleep(poll_s)
            raise ResponseTimeoutError(
                f"[{cls.name}] login not detected within {timeout_s}s."
            )
        finally:
            await provider.close()

    # ------------------------------------------------------------------ #
    # Send / receive
    # ------------------------------------------------------------------ #
    #: Substrings (lowercased) that mark a provider-side busy/limit message.
    busy_patterns: List[str] = [
        "high demand", "experiencing high", "at capacity", "rate limit",
        "too many requests", "try again later", "usage limit", "you've reached",
        "reached your limit", "temporarily unavailable", "overloaded",
    ]

    async def send(self, prompt: str, model: Optional[str] = None,
                   model_order: Optional[List[str]] = None) -> str:
        """Type ``prompt``, submit, wait for the reply, return its text.

        ``model`` / ``model_order`` override the config for this call.
        """
        await self.ensure_logged_in()
        order = model_order if model_order is not None else self.config.model_order
        single = model if model is not None else self.config.model
        order = order or ([single] if single else [])
        if order:
            await self.ensure_model(order)
        before = await self._assistant_count()

        await self._fill_input(prompt)
        await self._submit()
        await self._wait_for_new_message(before)
        text = await self._wait_for_stable_response()
        self._raise_if_busy(text)
        log.info("[%s] received %d chars", self.name, len(text))
        return text

    def _raise_if_busy(self, text: str) -> None:
        low = text.lower()
        for pat in self.busy_patterns:
            if pat in low:
                raise RateLimitedError(f"[{self.name}] provider busy/limited: {text[:160]}")

    # -------- model selection (overridable per provider) --------
    #: Button that opens the model picker menu (set per provider).
    model_picker_selector: Optional[str] = None

    async def select_model(self, model: str) -> bool:
        """Open the model picker and click the option matching ``model``.

        Returns True if an option was selected. Availability depends on the
        account (e.g. Grok gates Expert behind Premium), so a False result can
        just mean the model isn't offered.
        """
        if not self.model_picker_selector:
            return False
        page = self.page
        btn = page.locator(self.model_picker_selector).first
        if not await btn.count():
            return False
        await btn.click()
        await page.wait_for_timeout(400)
        pattern = re.compile(re.escape(model), re.I)
        for role in ("menuitemradio", "menuitem", "option"):
            opt = page.get_by_role(role, name=pattern).first
            try:
                if await opt.count():
                    await opt.click()
                    return True
            except Exception:
                pass
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    async def ensure_model(self, order: List[str]) -> Optional[str]:
        """Try each model in ``order``; the first one selectable wins."""
        for m in order:
            try:
                if await self.select_model(m):
                    log.info("[%s] selected model: %s", self.name, m)
                    return m
            except Exception as exc:  # noqa: BLE001
                log.debug("[%s] select_model(%s) failed: %s", self.name, m, exc)
        return None

    # Reading assistant replies — overridable per provider (DOMs drift / obfuscate).
    async def _assistant_count(self) -> int:
        return await self.page.locator(self.assistant_message_selector).count()

    async def _assistant_text(self) -> str:
        loc = self.page.locator(self.assistant_message_selector).last
        try:
            return (await loc.inner_text()).strip()
        except Exception:
            return ""

    async def _fill_input(self, text: str) -> None:
        el = self.page.locator(self.input_selector).last
        await el.click()
        if self.input_is_contenteditable:
            # fill() doesn't work on rich contenteditable (ProseMirror/Quill).
            await self.page.keyboard.insert_text(text)
        else:
            await el.fill(text)

    async def _submit(self) -> None:
        if self.send_button_selector:
            btn = self.page.locator(self.send_button_selector).last
            try:
                await btn.wait_for(state="visible", timeout=4_000)
                await btn.click()
                return
            except PWTimeoutError:
                log.debug("[%s] send button not clickable, falling back to Enter", self.name)
        await self.page.keyboard.press("Enter")

    async def _wait_for_new_message(self, before_count: int) -> None:
        deadline = time.monotonic() + self.config.response_timeout_ms / 1000
        while time.monotonic() < deadline:
            if await self._assistant_count() > before_count:
                return
            await asyncio.sleep(self.config.poll_interval_ms / 1000)
        raise ResponseTimeoutError(
            f"[{self.name}] no new assistant message appeared."
        )

    async def _wait_for_stable_response(self) -> str:
        """Poll the newest assistant block until its text stops changing."""
        page = self.page
        deadline = time.monotonic() + self.config.response_timeout_ms / 1000
        stability = self.config.stability_window_ms / 1000

        last_text = ""
        stable_since: Optional[float] = None
        while time.monotonic() < deadline:
            try:
                text = await self._assistant_text()
            except Exception:
                text = last_text
            streaming = False
            if self.streaming_indicator_selector:
                streaming = await page.locator(self.streaming_indicator_selector).count() > 0

            if text and text == last_text and not streaming:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= stability:
                    return text
            else:
                last_text = text
                stable_since = None
            await asyncio.sleep(self.config.poll_interval_ms / 1000)

        if last_text:
            log.warning("[%s] response timed out; returning last partial text", self.name)
            return last_text
        raise ResponseTimeoutError(f"[{self.name}] response never stabilized.")

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #
    async def screenshot(self, path: str, full_page: bool = True) -> str:
        await self.page.screenshot(path=path, full_page=full_page)
        return path

    async def save_session(self, out_path: str) -> str:
        """Persist the CURRENT context's cookies/localStorage to a file."""
        await self._context.storage_state(path=out_path)  # type: ignore[union-attr]
        return out_path
