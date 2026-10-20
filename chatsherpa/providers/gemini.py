"""Gemini (gemini.google.com) adapter.

Note: Google accounts are the hardest to automate-login (risk checks + 2FA).
Always authenticate via a headful ``capture_session`` run, not auto_login.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from ..base import BaseProvider

_CHAT = re.compile(r"/(?:u/\d+/)?app/([^/]+)")
_ACCOUNT = re.compile(r"/u/(\d+)/")
_ENTRY = re.compile(r"/(?:u/\d+/)?app/?")


class GeminiProvider(BaseProvider):
    name = "gemini"
    home_url = "https://gemini.google.com/app"

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

    # Gemini's composer is a Quill contenteditable (`.ql-editor`). Enter submits.
    input_selector = "div.ql-editor[contenteditable='true']"
    input_is_contenteditable = True
    send_button_selector = None  # Enter submits; the send button's label is localized
    # Responses render in <model-response>; the text sits in .model-response-text.
    assistant_message_selector = ".model-response-text"
    streaming_indicator_selector = None
    model_picker_selector = "bard-mode-switcher button, bard-mode-switcher"
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
