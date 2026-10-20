"""Grok (grok.com) adapter."""

from __future__ import annotations

from ..base import BaseProvider


class GrokProvider(BaseProvider):
    name = "grok"
    home_url = "https://grok.com/"

    # Grok's composer is a tiptap/ProseMirror contenteditable (the visible
    # <textarea> is an aria-hidden decoy). Enter does not submit — the Submit
    # button does.
    input_selector = 'div[contenteditable="true"][role="textbox"], div[contenteditable="true"]'
    input_is_contenteditable = True
    send_button_selector = 'button[aria-label="Submit"]'
    # Assistant turns are left-aligned (.items-start); user turns are .items-end.
    assistant_message_selector = '.items-start .response-content-markdown'
    # Model picker (e.g. Fast / Expert / Auto — Expert may be Premium-gated).
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

    async def _after_load(self) -> None:
        page = self.page
        for text in ("Accept all", "Got it", "I agree"):
            try:
                btn = page.get_by_role("button", name=text)
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(timeout=2_000)
            except Exception:
                pass
