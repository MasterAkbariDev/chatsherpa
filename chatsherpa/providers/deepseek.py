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
