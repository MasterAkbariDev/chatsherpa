"""Gemini (gemini.google.com) adapter.

Note: Google accounts are the hardest to automate-login (risk checks + 2FA).
Always authenticate via a headful ``capture_session`` run, not auto_login.
"""

from __future__ import annotations

from ..base import BaseProvider


class GeminiProvider(BaseProvider):
    name = "gemini"
    home_url = "https://gemini.google.com/app"

    # Gemini's composer is a Quill contenteditable (`.ql-editor`). Enter submits.
    input_selector = "div.ql-editor[contenteditable='true']"
    input_is_contenteditable = True
    send_button_selector = None  # Enter submits; the send button's label is localized
    # Responses render in <model-response>; the text sits in .model-response-text.
    assistant_message_selector = ".model-response-text"
    streaming_indicator_selector = None
    # Gemini's model switcher is a custom element (e.g. Flash / 2.5 Pro).
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
