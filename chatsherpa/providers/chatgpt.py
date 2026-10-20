"""ChatGPT (chatgpt.com) adapter."""

from __future__ import annotations

from ..base import BaseProvider


class ChatGPTProvider(BaseProvider):
    name = "chatgpt"
    home_url = "https://chatgpt.com/"

    # The composer is a ProseMirror contenteditable div (the #prompt-textarea id
    # was dropped in 2025 — match the class, keep the old id as a fallback).
    input_selector = 'div.ProseMirror[contenteditable="true"], #prompt-textarea'
    input_is_contenteditable = True
    send_button_selector = 'button[data-testid="send-button"], #composer-submit-button'
    # Legacy selector kept only as a fallback; the real read is JS-based below.
    assistant_message_selector = '[data-message-author-role="assistant"]'
    streaming_indicator_selector = 'button[data-testid="stop-button"], button[aria-label="Stop streaming"]'
    # Model picker (e.g. GPT-5, GPT-4o, …).
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
    login_wall_selectors = [
        'button[data-testid="login-button"]',
        'a[href*="auth/login"]',
    ]

    # Experimental auto-login (OpenAI auth flow). CAPTCHAs are common.
    login_trigger_selector = '[data-testid="login-button"]'
    email_input_selector = 'input[type="email"], input[name="username"], input[name="email"]'
    email_submit_selector = 'button[type="submit"], input[type="submit"]'
    password_input_selector = 'input[type="password"], input[name="password"]'
    password_submit_selector = 'button[type="submit"], input[type="submit"]'

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
