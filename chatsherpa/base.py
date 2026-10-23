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

from .display import VirtualDisplay
from .exceptions import (
    BotChallengeError,
    ChatFullError,
    LoginRequiredError,
    RateLimitedError,
    ResponseTimeoutError,
)
from .session import Session, SessionSource

log = logging.getLogger("chatsherpa")

_MORE_MODELS = re.compile(r"more models|see all models|show all", re.I)
_SKIP_MODEL = re.compile(
    r"^(premium|supergrok|upgrade\b.*|get\s.*|see\s.*|learn\s.*|try\s.*|subscribe.*|buy\s.*"
    r"|sign\s?(in|up).*|log\s?(in|out).*|settings|help|new chat|temporary chat|plans?|pricing)$",
    re.I,
)

_GENERIC_PICKER_JS = """() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none";
  };
  const nodes = document.querySelectorAll('[aria-haspopup="menu"], [aria-haspopup="listbox"]');
  for (const el of nodes) {
    if (!visible(el)) continue;
    const name = ((el.getAttribute("aria-label") || "") + " " + (el.innerText || "")).trim();
    const hint = name + " " + (typeof el.className === "string" ? el.className : "") + " " + (el.id || "");
    if (!/model/i.test(hint)) continue;
    if (/settings|help|account|sidebar|new chat/i.test(name)) continue;
    return el;
  }
  return null;
}"""

_READ_MODELS_JS = """({optionSelector, pickerSelector}) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none";
  };
  const firstLine = (el) => {
    const aria = (el.getAttribute("aria-label") || "").trim();
    const raw = aria || el.innerText || "";
    return (raw.split(/\\n/).map((s) => s.trim()).filter(Boolean)[0] || "");
  };
  const pushFrom = (root, selector, out) => {
    const visit = (node) => {
      if (!node || !node.querySelectorAll) return;
      let found = [];
      try { found = [...node.querySelectorAll(selector)]; } catch (e) { found = []; }
      for (const el of found) {
        if (!visible(el)) continue;
        const line = firstLine(el);
        if (line) out.push(line);
      }
      let all = [];
      try { all = [...node.querySelectorAll("*")]; } catch (e) { all = []; }
      for (const el of all) if (el.shadowRoot) visit(el.shadowRoot);
    };
    visit(root);
  };
  const labels = [];
  pushFrom(document, optionSelector, labels);
  if (!labels.length) {
    const panes = [...document.querySelectorAll(
      '[role="menu"], [role="listbox"], [role="dialog"], .cdk-overlay-pane, mat-menu-content'
    )].filter(visible);
    const pane = panes[panes.length - 1];
    if (pane) pushFrom(pane, "button, [role='option'], [role='menuitem'], a", labels);
  }
  if (!labels.length && pickerSelector) {
    const picker = document.querySelector(pickerSelector);
    const root = picker && (picker.shadowRoot || picker);
    if (root) pushFrom(root, "button, [role='option'], [role='menuitem']", labels);
  }
  return labels;
}"""


_MODEL_ARROWS = "▾▸▼⌄➤⌃ "


def clean_model_label(raw: Optional[str]) -> Optional[str]:
    """Normalize one model-picker label to the model name, or None.

    Handles localized "currently X" wrappers (e.g. Gemini's RTL
    "…، درحال‌حاضر Flash"), a leading "Model:" prefix, and trailing dropdown
    glyphs, so the model name can be shown in the status line.
    """
    if not raw:
        return None
    s = " ".join(raw.replace("arrow_drop_down", "").split()).strip()
    for marker in ("currently", "درحال‌حاضر",
                   "درحالحاضر"):
        idx = s.lower().find(marker.lower())
        if idx >= 0:
            s = s[idx + len(marker):].strip(" :،,")
    s = re.sub(r"^model:?\s*", "", s, flags=re.I).strip()
    s = s.strip(_MODEL_ARROWS)
    return s or None


def clean_model_labels(raw: List[str]) -> List[str]:
    """Keep the short names a model menu actually offers."""
    seen = set()
    labels: List[str] = []
    for item in raw:
        label = re.sub(r"^model:\s*", "", (item or "").strip(), flags=re.I).strip()
        if not label or len(label) > 48 or _SKIP_MODEL.match(label):
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return labels


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

    # Fallback only, for providers with NO done-signal (``has_ready_signal`` is
    # False): a response is "done" once its text stops changing for this long.
    # Providers that expose ``_response_ready`` ignore this and return on the
    # signal, so a mid-stream pause can never be read as "done".
    stability_window_ms: int = 900
    # Polling granularity for new-message / stability / settle detection. Kept
    # tight so a finished reply is noticed quickly instead of up to a full poll
    # late — the dominant library tail once a done-signal has fired.
    poll_interval_ms: int = 120
    # Settle once a provider's done-signal fires (copy button, action toolbar).
    ready_settle_ms: int = 350
    # Safety net for ``has_ready_signal`` providers: if the done-signal never
    # fires (e.g. the UI drifted and its selector broke) but the text has sat
    # completely unchanged this long, return anyway. Long enough that real
    # streaming never trips it, short enough to bound a broken selector to a few
    # seconds instead of the full response timeout.
    ready_fallback_ms: int = 6_000

    # Extra args passed to chromium.launch (e.g. ["--no-sandbox"]).
    launch_args: List[str] = field(default_factory=list)

    # Model selection: a single preferred model, or an ordered preference list
    # (the first one that can be selected wins). Names must match the signed-in
    # account's picker labels; `chatsherpa models` reads those from the page.
    model: Optional[str] = None
    model_order: Optional[List[str]] = None


class BaseProvider:
    """Abstract driver for a single AI chat web UI.

    Subclasses MUST set: ``name``, ``home_url``, ``input_selector``,
    ``assistant_message_selector``. Everything else has a sensible default.
    """

    name: str = "base"
    home_url: str = ""

    @classmethod
    def is_conversation(cls, url: str) -> bool:
        """True when ``url`` is an existing chat, not the new-chat home page."""
        return False

    @classmethod
    def entry_url(cls, url: str) -> bool:
        """True when ``url`` is a safe page to open instead of the home page."""
        return cls.is_conversation(url)

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
    #: True when the provider overrides :meth:`_response_ready` with a reliable
    #: turn-finished signal. Such providers return on the signal (plus a short
    #: settle) and never end a reply on the bare stability window, so a
    #: mid-stream pause is never mistaken for a completed turn.
    has_ready_signal: bool = False
    # Seconds to wait for a sent message to show up in the chat before sending it
    # once more. Well under the response timeout, so a lost send is retried early.
    SEND_CONFIRM_S: float = 30.0
    # Page text that means this chat can take no more messages, for example
    # ChatGPT's "Chat memory full". Matched against the visible page text.
    chat_full_markers: tuple = ()
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
        self._display = VirtualDisplay()
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
        launch_headless = self.config.headless
        if not self.config.headless and self._display.start_if_needed():
            launch_headless = False
        log.info("[%s] launching chromium (headless=%s)", self.name, launch_headless)
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=launch_headless,
            slow_mo=self.config.slow_mo,
            args=[*self.config.launch_args, "--disable-blink-features=AutomationControlled"],
            proxy=self.config.proxy,  # type: ignore[arg-type]
            ignore_default_args=["--enable-automation"],
        )

        ctx_kwargs: Dict[str, object] = {}
        if self.config.user_agent:
            ctx_kwargs["user_agent"] = self.config.user_agent
        if self.config.locale:
            ctx_kwargs["locale"] = self.config.locale
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
        self._display.stop()
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
            # A cold browser needs about 35-60s before the composer renders (measured
            # on the VPS); the 8s login_wait_ms default reported valid sessions as logged out.
            await page.wait_for_selector(
                self.input_selector, timeout=max(self.config.login_wait_ms, 60_000), state="visible"
            )
            return True
        except PWTimeoutError:
            return False

    #: Page text of a bot check (Cloudflare and similar). Seen as "not logged in"
    #: unless recognised, which sends the user to sign in for no reason.
    challenge_markers: tuple = ("Just a moment", "Verify you are human", "Checking your browser")

    async def ensure_logged_in(self) -> None:
        if not await self.is_logged_in():
            text = await self._page_text()
            if any(m in text for m in self.challenge_markers):
                raise BotChallengeError(
                    f"[{self.name}] blocked by a bot check at {self.page.url}. This is the "
                    "server's browser or IP, not the login: run it headed (Xvfb) or from another IP."
                )
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
                   model_order: Optional[List[str]] = None, on_model=None) -> str:
        """Type ``prompt``, submit, wait for the reply, return it as Markdown.

        ``model`` / ``model_order`` override the config for this call. ``on_model``
        is called once with the model name actually in use (for live status).
        """
        await self.ensure_logged_in()
        try:
            await self.page.locator(self.input_selector).last.wait_for(state="visible", timeout=60_000)
        except Exception:
            pass
        await self._raise_if_chat_full()
        order = model_order if model_order is not None else self.config.model_order
        single = model if model is not None else self.config.model
        order = order or ([single] if single else [])
        selected = await self.ensure_model(order) if order else None
        if on_model:
            try:
                name = selected or await self.current_model()
                if name:
                    on_model(name)
            except Exception:
                pass
        before = await self._baseline_assistant_count()

        await self._fill_input(prompt)
        await self._submit()
        try:
            await self._wait_for_new_message(before, timeout_s=self.SEND_CONFIRM_S)
        except ResponseTimeoutError:
            # Nothing new reached the chat, so the message did not register.
            # Clear the composer and send once more; this cannot duplicate a
            # message because the chat still has no new turn.
            log.warning("[%s] message did not register (%s); sending once more",
                        self.name, await self._composer_state())
            await self._clear_input()
            await self._fill_input(prompt)
            await self._submit()
            await self._wait_for_new_message(before)
        text = await self._wait_for_stable_response()
        result = (await self._assistant_markdown()) or text
        self._raise_if_busy(result)
        log.info("[%s] received %d chars", self.name, len(result))
        return result

    def _raise_if_busy(self, text: str) -> None:
        low = text.lower()
        for pat in self.busy_patterns:
            if pat in low:
                raise RateLimitedError(f"[{self.name}] provider busy/limited: {text[:160]}")

    # -------- model selection (overridable per provider) --------
    #: Button that opens the model picker menu (set per provider).
    model_picker_selector: Optional[str] = None
    #: Menu rows to read after the picker is open. The labels come from the page.
    model_option_selector: str = (
        '[role="menuitem"], [role="menuitemradio"], [role="option"], [role="radio"]'
    )

    async def list_models(self) -> List[str]:
        """Open the signed-in account's model menu and return its labels."""
        await self.ensure_logged_in()
        if not await self._open_model_menu():
            return []
        labels = clean_model_labels(await self._read_model_labels())
        more = next((item for item in labels if _MORE_MODELS.search(item)), None)
        if more:
            try:
                await self.page.get_by_role(
                    "menuitem", name=re.compile(re.escape(more), re.I)
                ).first.click(timeout=3_000)
                await self.page.wait_for_timeout(400)
                labels = clean_model_labels(await self._read_model_labels())
            except Exception:
                pass
        try:
            await self.page.keyboard.press("Escape")
        except Exception:
            pass
        return [item for item in labels if not _MORE_MODELS.search(item)]

    async def _open_model_menu(self) -> bool:
        page = self.page
        if self.model_picker_selector:
            deadline = time.monotonic() + 8
            loc = page.locator(self.model_picker_selector).first
            while time.monotonic() < deadline:
                try:
                    if await loc.count() and await loc.is_visible():
                        await loc.click(timeout=3_000)
                        await page.wait_for_timeout(400)
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.4)
            return False
        handle = await page.evaluate_handle(_GENERIC_PICKER_JS)
        element = handle.as_element()
        if element is None:
            return False
        try:
            await element.click(timeout=3_000)
            await page.wait_for_timeout(400)
            return True
        except Exception:
            return False

    async def _read_model_labels(self) -> List[str]:
        raw = await self.page.evaluate(
            _READ_MODELS_JS,
            {
                "optionSelector": self.model_option_selector,
                "pickerSelector": self.model_picker_selector or "",
            },
        )
        return [item for item in raw or [] if isinstance(item, str)]

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

    async def current_model(self) -> Optional[str]:
        """Best-effort name of the model the UI currently has selected, read from
        the model-picker button without opening the menu. Cheap; may be None."""
        if not self.model_picker_selector:
            return None
        try:
            btn = self.page.locator(self.model_picker_selector).first
            if not await btn.count():
                return None
            text = (await btn.inner_text() or "").strip()
            label = clean_model_label(text) or clean_model_label(await btn.get_attribute("aria-label") or "")
            return label
        except Exception:
            return None

    # Serialize a DOM element to Markdown so replies keep structure (code blocks,
    # quotes, lists, links, images) instead of flattening to plain text.
    _MD_JS = r"""(el) => {
      if (!el) return '';
      const walk = (node) => {
        if (node.nodeType === 3) return node.textContent;
        if (node.nodeType !== 1) return '';
        const tag = node.tagName.toLowerCase();
        // Drop non-content chrome: code-block toolbars (Copy/Download), icons, etc.
        if (tag === 'button' || tag === 'svg' || tag === 'style' || tag === 'script') return '';
        if (node.getAttribute('role') === 'button' || node.getAttribute('aria-hidden') === 'true') return '';
        const kids = () => Array.from(node.childNodes).map(walk).join('');
        switch (tag) {
          case 'br': return '\n';
          case 'strong': case 'b': return '**' + kids() + '**';
          case 'em': case 'i': return '*' + kids() + '*';
          case 'code':
            if (node.closest('pre')) return node.textContent;
            return '`' + node.textContent + '`';
          case 'pre': {
            const codeEl = node.querySelector('code');
            const m = codeEl && (codeEl.className.match(/language-([\w+-]+)/) || []);
            const lang = (m && m[1]) || '';
            return '\n```' + lang + '\n' + node.innerText.replace(/\n+$/, '') + '\n```\n';
          }
          case 'blockquote':
            return '\n' + node.innerText.split('\n').map(l => '> ' + l).join('\n') + '\n';
          case 'h1': return '\n# ' + kids().trim() + '\n';
          case 'h2': return '\n## ' + kids().trim() + '\n';
          case 'h3': return '\n### ' + kids().trim() + '\n';
          case 'h4': case 'h5': case 'h6': return '\n#### ' + kids().trim() + '\n';
          case 'ul': return '\n' + Array.from(node.children).map(li => '- ' + walk(li).trim()).join('\n') + '\n';
          case 'ol': return '\n' + Array.from(node.children).map((li, i) => (i + 1) + '. ' + walk(li).trim()).join('\n') + '\n';
          case 'li': return kids();
          case 'a': { const h = node.getAttribute('href'); const t = kids().trim(); return h ? '[' + t + '](' + h + ')' : t; }
          case 'img': { const s = node.getAttribute('src') || ''; const a = node.getAttribute('alt') || 'image'; return '![' + a + '](' + s + ')'; }
          case 'hr': return '\n---\n';
          case 'p': case 'div': return kids() + '\n';
          default: return kids();
        }
      };
      return walk(el).replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
    }"""

    async def _assistant_markdown(self) -> str:
        """The latest reply as Markdown (structure preserved). Falls back to text."""
        try:
            loc = self.page.locator(self.assistant_message_selector).last
            if await loc.count():
                handle = await loc.element_handle()
                md = await self.page.evaluate(self._MD_JS, handle)
                if md and md.strip():
                    return md.strip()
        except Exception:
            pass
        return await self._assistant_text()

    async def _fill_input(self, text: str) -> None:
        el = self.page.locator(self.input_selector).last
        try:
            await el.click(timeout=15_000)
        except PWTimeoutError:
            # Something sits on top of the composer. A full chat shows its banner there,
            # so report that; otherwise focus the composer directly (focus ignores overlays).
            await self._raise_if_chat_full()
            await el.focus()
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

    async def _baseline_assistant_count(self) -> int:
        """Count existing assistant turns, waiting out lazy render on cold pages.

        On a freshly-navigated *conversation* page the turn markup can hydrate a
        beat after load, so reading the count too early returns 0. If the next
        reply's "new message" check used that stale 0 it would fire the instant
        the OLD turns render in, and a completed prior turn (its done-signal
        already satisfied) would be returned instead of the real reply. Wait for
        the count to settle to a steady non-zero value first. On a warm page the
        count is already stable, so this costs a single extra poll; on a genuine
        new-chat page (no prior turns) it returns 0 immediately.
        """
        count = await self._assistant_count()
        try:
            on_conversation = type(self).is_conversation(self.page.url)
        except Exception:
            on_conversation = False
        if not on_conversation:
            return count
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            await asyncio.sleep(self.config.poll_interval_ms / 1000)
            nxt = await self._assistant_count()
            if nxt == count and count > 0:
                return count
            count = nxt
        return count

    async def _wait_for_new_message(self, before_count: int, timeout_s: Optional[float] = None) -> None:
        wait = self.config.response_timeout_ms / 1000 if timeout_s is None else timeout_s
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if await self._assistant_count() > before_count and await self._send_registered():
                return
            await asyncio.sleep(self.config.poll_interval_ms / 1000)
        await self._raise_if_chat_full()
        raise ResponseTimeoutError(
            f"[{self.name}] no new assistant message appeared (page: {self.page.url})."
        )

    async def _send_registered(self) -> bool:
        """Provider hook: True once the sent message is actually in the chat.

        The default trusts the new-turn count. A provider whose page can show an
        older reply as new (or where a send silently fails) overrides this.
        """
        return True

    async def _page_text(self) -> str:
        try:
            return await self.page.evaluate(
                "() => (document.title || '') + '\\n' + (document.body ? document.body.innerText : '')")
        except Exception:
            return ""

    async def _raise_if_chat_full(self) -> None:
        if not self.chat_full_markers:
            return
        text = await self._page_text()
        for marker in self.chat_full_markers:
            if marker in text:
                raise ChatFullError(
                    f"[{self.name}] this chat is full on the provider's side ({marker!r}). "
                    f"Start a new chat: pass a new --chat name, or --new to reset this one."
                )

    async def _composer_state(self) -> str:
        """One-line description of the composer and send control, for logs."""
        try:
            text = await self.page.locator(self.input_selector).last.evaluate(
                "e => (e.innerText || e.value || '').trim()")
        except Exception as exc:
            return f"composer unreadable: {type(exc).__name__}"
        state = f"composer chars={len(text)}"
        if self.send_button_selector:
            try:
                btn = self.page.locator(self.send_button_selector).last
                visible = await btn.is_visible()
                enabled = await btn.is_enabled() if visible else False
                state += f", send visible={visible} enabled={enabled}"
            except Exception:
                state += ", send unreadable"
        return state

    async def _clear_input(self) -> None:
        """Empty the composer so a resend does not append to a half-typed prompt."""
        try:
            await self.page.locator(self.input_selector).last.click()
            await self.page.keyboard.press("ControlOrMeta+A")
            await self.page.keyboard.press("Delete")
        except Exception:
            pass

    async def _is_streaming(self) -> bool:
        """True only when a stop control is actually visible, not merely in the DOM."""
        sel = self.streaming_indicator_selector
        if not sel:
            return False
        loc = self.page.locator(sel)
        try:
            count = await loc.count()
        except Exception:
            return False
        for i in range(min(count, 6)):
            try:
                if await loc.nth(i).is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _response_ready(self) -> bool:
        """Provider hook: the latest turn has finished generating."""
        return False

    async def _wait_for_stable_response(self) -> str:
        """Wait until the newest assistant turn is genuinely finished, then return it.

        Two regimes, chosen by :attr:`has_ready_signal`:

        * **Done-signal providers** return once :meth:`_response_ready` fires and
          the text has held steady for ``ready_settle_ms``. The bare stability
          window is NOT used to end the turn, so a mid-stream pause can never be
          mistaken for completion (no truncation). ``ready_fallback_ms`` of fully
          unchanged text is a safety net for a drifted/broken signal selector.
        * **Signal-less providers** fall back to the old behaviour: the text
          holding steady for ``stability_window_ms`` while no streaming indicator
          is visible marks the turn done.
        """
        deadline = time.monotonic() + self.config.response_timeout_ms / 1000
        stability = self.config.stability_window_ms / 1000
        settle = self.config.ready_settle_ms / 1000
        fallback = self.config.ready_fallback_ms / 1000

        last_text = ""
        ready_since: Optional[float] = None   # done-signal held + text steady
        stable_since: Optional[float] = None  # text unchanged (window / fallback)
        while time.monotonic() < deadline:
            try:
                text = await self._assistant_text()
            except Exception:
                text = last_text

            if text != last_text:
                # Any change in the reply resets every timer: still streaming.
                last_text = text
                ready_since = stable_since = None
                await asyncio.sleep(self.config.poll_interval_ms / 1000)
                continue

            now = time.monotonic()
            if self.has_ready_signal:
                if text and await self._response_ready():
                    # Done-signal fired and the text is steady: settle and return.
                    if ready_since is None:
                        ready_since = now
                    elif now - ready_since >= settle:
                        return text
                    stable_since = None
                else:
                    ready_since = None
                    # Safety net: the signal never came but nothing has changed
                    # for a long time — the UI's done marker likely drifted.
                    if text:
                        if stable_since is None:
                            stable_since = now
                        elif now - stable_since >= fallback:
                            log.warning("[%s] done-signal never fired; returning "
                                        "after %dms of stable text", self.name,
                                        self.config.ready_fallback_ms)
                            return text
            else:
                streaming = await self._is_streaming()
                if text and not streaming:
                    if stable_since is None:
                        stable_since = now
                    elif now - stable_since >= stability:
                        return text
                else:
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
