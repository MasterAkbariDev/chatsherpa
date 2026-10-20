"""ChatGPT (chatgpt.com) adapter."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

from ..base import BaseProvider


class ChatGPTProvider(BaseProvider):
    name = "chatgpt"
    home_url = "https://chatgpt.com/"

    @classmethod
    def is_conversation(cls, url: str) -> bool:
        return urlparse(url).path.startswith("/c/")

    # The logged-out marketing page also renders a composer, so login is decided
    # by the session endpoint / profile button below — not by this field existing.
    input_selector = (
        '#prompt-textarea, div.ProseMirror[contenteditable="true"], '
        '[data-testid="prompt-textarea"], textarea[name="prompt-textarea"], '
        '[contenteditable="true"][data-lexical-editor="true"]'
    )
    input_is_contenteditable = True
    send_button_selector = 'button[data-testid="send-button"], #composer-submit-button'
    # Legacy selector kept only as a fallback; the real read is JS-based below.
    assistant_message_selector = '[data-message-author-role="assistant"]'
    streaming_indicator_selector = 'button[data-testid="stop-button"], button[aria-label="Stop streaming"]'
    model_picker_selector = '[data-testid="model-switcher-dropdown-button"]'

    # ChatGPT's turn markup is obfuscated (no stable data-role/class). The one
    # durable anchor is the screen-reader label "ChatGPT said:". Count and read
    # assistant turns relative to it.
    _COUNT_JS = (
        "() => [...document.querySelectorAll('.sr-only')]"
        ".filter(h => h.textContent.trim().startsWith('ChatGPT said:')).length"
    )
    _TEXT_JS = """() => {
        const labels = [...document.querySelectorAll('.sr-only')]
            .filter(h => h.textContent.trim().startsWith('ChatGPT said:'));
        if (!labels.length) return '';
        const last = labels[labels.length - 1];
        const turn = last.closest('[data-turn-key]') || last.parentElement;
        let txt = turn ? turn.innerText : '';
        const i = txt.lastIndexOf('ChatGPT said:');
        if (i >= 0) txt = txt.slice(i + 'ChatGPT said:'.length);
        return txt.trim();
    }"""

    async def _assistant_count(self) -> int:
        try:
            return int(await self.page.evaluate(self._COUNT_JS))
        except Exception:
            return 0

    async def _assistant_text(self) -> str:
        try:
            return (await self.page.evaluate(self._TEXT_JS)).strip()
        except Exception:
            return ""

    # Copy appears on a turn only after generation finishes. A hidden stop
    # button left in the DOM must not keep the CLI waiting.
    _TURN_DONE_JS = """() => {
        const labels = [...document.querySelectorAll('.sr-only')]
            .filter(h => h.textContent.trim().startsWith('ChatGPT said:'));
        if (!labels.length) return false;
        const turn = labels[labels.length - 1].closest('[data-turn-key]')
            || labels[labels.length - 1].parentElement;
        if (!turn) return false;
        const copy = turn.querySelector(
            '[data-testid="copy-turn-action-button"], button[aria-label="Copy"]'
        );
        if (!copy) return false;
        const r = copy.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }"""

    async def _response_ready(self) -> bool:
        try:
            return bool(await self.page.evaluate(self._TURN_DONE_JS))
        except Exception:
            return False
    login_wall_selectors = [
        'button[data-testid="login-button"]',
    ]
    # Present on the signed-in app, not on the logged-out landing page.
    _AUTHED_UI = '[data-testid="profile-button"], nav[aria-label="Chat history"]'
    _SESSION_JS = """async () => {
        try {
            const res = await fetch('/api/auth/session', { credentials: 'include' });
            if (!res.ok) return false;
            const data = await res.json();
            return !!(data && data.user);
        } catch (e) {
            return false;
        }
    }"""

    # Experimental auto-login (OpenAI auth flow). CAPTCHAs are common.
    login_trigger_selector = '[data-testid="login-button"]'
    email_input_selector = 'input[type="email"], input[name="username"], input[name="email"]'
    email_submit_selector = 'button[type="submit"], input[type="submit"]'
    password_input_selector = 'input[type="password"], input[name="password"]'
    password_submit_selector = 'button[type="submit"], input[type="submit"]'

    async def _visible(self, selector: str) -> bool:
        try:
            return await self.page.locator(selector).first.is_visible()
        except Exception:
            return False

    async def is_logged_in(self) -> bool:
        """Signed in when the session API returns a user, or the app shell is up.

        The logged-out page shows a login button immediately, including while a
        valid session is still hydrating, so that button is not a final answer.
        """
        deadline = time.monotonic() + max(self.config.login_wait_ms, 20_000) / 1000
        while True:
            try:
                if await self.page.evaluate(self._SESSION_JS):
                    return True
            except Exception:
                pass
            if await self._visible(self._AUTHED_UI):
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.5)

    async def _after_load(self) -> None:
        page = self.page
        # Dismiss the occasional "stay logged out" / welcome dialogs.
        for text in ("Stay logged out", "Okay, let’s go", "Got it"):
            try:
                btn = page.get_by_role("button", name=text)
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(timeout=2_000)
            except Exception:
                pass
