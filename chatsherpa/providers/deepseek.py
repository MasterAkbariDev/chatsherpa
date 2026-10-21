"""DeepSeek (chat.deepseek.com) adapter.

DeepSeek ships heavily hashed CSS class names, so the response selector is the
most fragile part. It is exposed as a normal class attribute — override it via
the ``selectors=`` constructor arg if the UI changes:

    DeepSeekProvider(session, selectors={"assistant_message_selector": "..."})
"""

from __future__ import annotations

from urllib.parse import urlparse

from ..base import BaseProvider


class DeepSeekProvider(BaseProvider):
    name = "deepseek"
    home_url = "https://chat.deepseek.com/"

    @classmethod
    def is_conversation(cls, url: str) -> bool:
        return "/a/chat/" in urlparse(url).path

    # Composer is a plain <textarea> with placeholder "Message DSeek" (the old
    # #chat-input id is gone). Enter submits, so no send button is needed.
    input_selector = 'textarea[placeholder^="Message"]'
    input_is_contenteditable = False
    send_button_selector = None
    # Assistant markdown lives in `.ds-markdown`; user turns don't use it.
    assistant_message_selector = ".ds-markdown"

    # DeepSeek ships no stop/streaming indicator, so without a done-signal every
    # reply waited the full stability window. The per-turn action toolbar
    # (`.ds-button--iconLabelTertiary`: copy / regenerate / read-aloud) renders
    # only once generation finishes. Scope strictly to the LAST turn — stop
    # climbing before an ancestor spans more than one `.ds-markdown`, so a prior
    # completed turn's toolbar can never be mistaken for this one's.
    _TURN_DONE_JS = """() => {
        const vis = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0
                && s.visibility !== 'hidden' && s.display !== 'none';
        };
        const nodes = [...document.querySelectorAll('.ds-markdown')];
        const last = nodes[nodes.length - 1];
        if (!last) return false;
        let box = last, cur = last.parentElement, up = 0;
        while (cur && up < 6) {
            if (cur.querySelectorAll('.ds-markdown').length > 1) break;
            box = cur; cur = cur.parentElement; up++;
        }
        const btn = box.querySelector('.ds-button--iconLabelTertiary');
        return !!(btn && vis(btn));
    }"""

    has_ready_signal = True

    async def _response_ready(self) -> bool:
        try:
            return bool(await self.page.evaluate(self._TURN_DONE_JS))
        except Exception:
            return False
    # DeepSeek has no stable model-picker selector. list_models still looks
    # for a popup whose label mentions "model".
    login_wall_selectors = [
        'input[type="password"]',
        'text="Log in"',
    ]

    # Experimental auto-login (email + password on one form).
    email_input_selector = 'input[type="text"], input[placeholder*="mail" i], input[name="email"]'
    password_input_selector = 'input[type="password"]'
    password_submit_selector = 'div[role="button"]:has-text("Log in"), button:has-text("Log in")'
