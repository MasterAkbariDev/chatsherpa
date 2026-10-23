"""Grok (grok.com) adapter."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

from ..base import BaseProvider


class GrokProvider(BaseProvider):
    name = "grok"
    home_url = "https://grok.com/"

    @classmethod
    def is_conversation(cls, url: str) -> bool:
        path = urlparse(url).path.strip("/")
        return path.startswith("chat/") or path.startswith("c/")

    # Grok's composer is a tiptap/ProseMirror contenteditable (the visible
    # <textarea> is an aria-hidden decoy). Enter does not submit — the Submit
    # button does.
    input_selector = 'div[contenteditable="true"][role="textbox"], div[contenteditable="true"]'
    input_is_contenteditable = True
    send_button_selector = 'button[aria-label="Submit"]'
    # Assistant turns are left-aligned (.items-start); user turns are .items-end.
    assistant_message_selector = '.items-start .response-content-markdown'
    model_picker_selector = 'button[aria-label^="Model:"]'
    login_wall_selectors = [
        'a[href*="/sign-in"]',
        'text="Sign in"',
    ]

    # Experimental auto-login. NOTE: Grok sign-in is often OAuth-only
    # (X / Google / Apple), which email+password cannot drive — in that case
    # auto-login will fail and you should use headful capture.
    login_trigger_selector = 'a[href*="/sign-in"], button:has-text("Sign in")'
    email_input_selector = 'input[type="email"], input[name="email"]'
    email_submit_selector = 'button[type="submit"]'
    password_input_selector = 'input[type="password"]'
    password_submit_selector = 'button[type="submit"]'

    #: How long the composer and a "Sign in" wall may both be visible before we call the
    #: session logged out. Grok shows the composer to logged-out visitors too, so the composer
    #: alone is not a sign-in. A logged-in page drops the wall once the header hydrates.
    _WALL_GRACE_S = 6.0

    async def _login_wall_visible(self) -> bool:
        for sel in self.login_wall_selectors:
            try:
                loc = self.page.locator(sel)
                if await loc.count() and await loc.first.is_visible():
                    return True
            except Exception:
                pass
        return False

    async def is_logged_in(self) -> bool:
        """Logged in means the composer is visible and no sign-in wall is. Grok's SPA hydrates
        slowly on a server, so the wall can show briefly before the header renders. Poll until
        the composer appears; then give the wall a grace period to clear before deciding."""
        deadline = time.monotonic() + max(self.config.login_wait_ms, 60_000) / 1000
        composer = self.page.locator(self.input_selector)
        wall_since: float | None = None
        while time.monotonic() < deadline:
            try:
                has_composer = bool(await composer.count()) and await composer.first.is_visible()
            except Exception:
                has_composer = False
            if has_composer:
                if not await self._login_wall_visible():
                    return True
                wall_since = wall_since or time.monotonic()
                if time.monotonic() - wall_since > self._WALL_GRACE_S:
                    return False
            else:
                wall_since = None
            await asyncio.sleep(0.4)
        return False

    # Cookie banners cover the composer and its send button. Decline non-essential
    # cookies first (the privacy-preserving choice), then accept as a fallback.
    _CONSENT_LABELS = ("Reject all", "Reject", "Decline", "Only necessary",
                       "Accept all", "Got it", "I agree")

    async def _dismiss_consent(self) -> None:
        for text in self._CONSENT_LABELS:
            try:
                btn = self.page.get_by_role("button", name=text)
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(timeout=2_000)
                    await asyncio.sleep(0.3)
            except Exception:
                pass

    async def _after_load(self) -> None:
        await self._dismiss_consent()

    async def _fill_input(self, text: str) -> None:
        # The banner can appear after load; clear it right before typing.
        await self._dismiss_consent()
        await super()._fill_input(text)
