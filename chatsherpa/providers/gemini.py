"""Gemini (gemini.google.com) adapter.

Note: Google accounts are the hardest to automate-login (risk checks + 2FA).
Always authenticate via a headful ``capture_session`` run, not auto_login.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Optional
from urllib.parse import urlparse

from ..base import BaseProvider

_CHAT = re.compile(r"/(?:u/\d+/)?app/([^/]+)")
_ACCOUNT = re.compile(r"/u/(\d+)/")
_ENTRY = re.compile(r"/(?:u/\d+/)?app/?")


class GeminiProvider(BaseProvider):
    name = "gemini"
    home_url = "https://gemini.google.com/app"
    # Google answers the datacenter IP with a 403 page; that is a block, not a login.
    challenge_markers = ("Error 403 (Forbidden)", "does not have permission to get URL")

    @classmethod
    def account_index(cls, url: str) -> Optional[int]:
        """Google account slot in a Gemini URL. Missing ``/u/N/`` means the main account."""
        host = urlparse(url).hostname or ""
        if "gemini.google.com" not in host:
            return None
        match = _ACCOUNT.search(urlparse(url).path)
        return int(match.group(1)) if match else 0

    @classmethod
    def account_home(cls, index: int) -> str:
        return f"https://gemini.google.com/u/{index}/app"

    @classmethod
    def is_conversation(cls, url: str) -> bool:
        match = _CHAT.search(urlparse(url).path)
        return bool(match and match.group(1))

    @classmethod
    def entry_url(cls, url: str) -> bool:
        host = urlparse(url).hostname or ""
        if "gemini.google.com" not in host:
            return False
        path = urlparse(url).path
        return cls.is_conversation(url) or bool(_ENTRY.fullmatch(path))

    #: Google's account button. It is on the page only when someone is signed in. Its label is translated
    #: (a Persian page does not say "Google Account"), so the link to Google's sign-out options is also checked:
    #: that address is the same in every language.
    _SIGNED_IN = '[aria-label*="Google Account" i]'
    _ACCOUNT_LINK = 'a[href*="accounts.google.com/SignOutOptions"]'
    #: A link to Google's sign-in, by its address. A signed-out Gemini page shows it next to the composer.
    #: Matched by address, not by text, because the words change with the page's language.
    _SIGN_IN_WALL = 'a[href*="ServiceLogin"], a[href*="accounts.google.com/signin"]'
    #: How long a composer may sit next to a "Sign in" link before the session counts as signed out.
    _WALL_GRACE_S = 4.0

    async def _has_account(self) -> bool:
        """Google's account button, by its label or by its link: either one means someone is signed in."""
        if await self.page.locator(self._SIGNED_IN).count():
            return True
        return bool(await self.page.locator(self._ACCOUNT_LINK).count())

    async def is_logged_in(self) -> bool:
        """Signed in means the composer is visible, Google's account button is on the page, and no
        "Sign in" link is. A signed-out Gemini page also shows a composer, so the composer alone
        is not a sign-in (the same false "connected" Grok had)."""
        # A signed-in Gemini page shows its composer within seconds; do not keep the person waiting longer.
        deadline = time.monotonic() + max(self.config.login_wait_ms, 20_000) / 1000
        wall_since: float | None = None
        while time.monotonic() < deadline:
            try:
                composer = self.page.locator(self.input_selector)
                has_composer = bool(await composer.count()) and await composer.first.is_visible()
                signed_in = await self._has_account()
                wall = bool(await self.page.locator(self._SIGN_IN_WALL).count())
            except Exception:
                has_composer, signed_in, wall = False, False, False
            if has_composer and signed_in and not wall:
                return True
            if has_composer and wall:
                wall_since = wall_since or time.monotonic()
                if time.monotonic() - wall_since > self._WALL_GRACE_S:
                    return False
            else:
                wall_since = None
            await asyncio.sleep(0.4)
        return False

    async def diagnose(self) -> str:
        """What the page shows, in words, for a sign-in that was not recognised: the same three signals
        is_logged_in reads, and the address's host and path (no query, no account ids)."""
        try:
            composer = self.page.locator(self.input_selector)
            has_composer = bool(await composer.count()) and await composer.first.is_visible()
            account = await self._has_account()
            wall = bool(await self.page.locator(self._SIGN_IN_WALL).count())
        except Exception as exc:  # noqa: BLE001 - a page that cannot be read says so
            return f"the page could not be read ({str(exc)[:80]})"
        where = urlparse(self.page.url)
        return (
            f"page {where.hostname or ''}{where.path}; "
            f"message box {'shown' if has_composer else 'not shown'}, "
            f"Google account button {'found' if account else 'not found'}, "
            f"sign-in link {'shown' if wall else 'not shown'}"
        )

    # Gemini's composer is a Quill contenteditable (`.ql-editor`). Enter submits.
    input_selector = "div.ql-editor[contenteditable='true']"
    input_is_contenteditable = True
    send_button_selector = None  # Enter submits; the send button's label is localized
    # Responses render in <model-response>; the text sits in .model-response-text.
    assistant_message_selector = ".model-response-text"
    # Pictures Gemini makes sit beside the text, inside the whole response.
    media_selector = "model-response"
    streaming_indicator_selector = None  # Gemini's "Stop" button vanishes mid-stream, so it is unreliable.
    model_picker_selector = "bard-mode-switcher button, bard-mode-switcher"

    # No reliable streaming indicator, so a done-signal is what stops Gemini from
    # waiting the full stability window (and guards against a mid-stream pause
    # being read as "done"). The response action bar — a visible Copy button —
    # appears only once the turn finishes. Scope strictly to the LAST turn: stop
    # climbing before an ancestor spans more than one `.model-response-text`.
    _TURN_DONE_JS = """() => {
        const vis = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0
                && s.visibility !== 'hidden' && s.display !== 'none';
        };
        const nodes = [...document.querySelectorAll('.model-response-text')];
        const last = nodes[nodes.length - 1];
        if (!last) return false;
        let box = last, cur = last.parentElement, up = 0;
        while (cur && up < 8) {
            if (cur.querySelectorAll('.model-response-text').length > 1) break;
            box = cur; cur = cur.parentElement; up++;
        }
        const btn = box.querySelector('button[aria-label="Copy"]');
        return !!(btn && vis(btn));
    }"""

    has_ready_signal = True

    async def _response_ready(self) -> bool:
        try:
            return bool(await self.page.evaluate(self._TURN_DONE_JS))
        except Exception:
            return False

    login_wall_selectors = [
        'a[href*="accounts.google.com"]',
        'text="Sign in"',
    ]

    # Experimental auto-login via Google accounts. In practice Google almost
    # always demands 2FA / device verification and blocks headless logins —
    # headful capture is the realistic path here.
    login_trigger_selector = 'a[href*="accounts.google.com"]'
    email_input_selector = 'input[type="email"]'
    email_submit_selector = '#identifierNext button, button:has-text("Next")'
    password_input_selector = 'input[type="password"]'
    password_submit_selector = '#passwordNext button, button:has-text("Next")'
