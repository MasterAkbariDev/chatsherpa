"""Bot checks and browser-start failures get their own error types (no browser)."""

from types import SimpleNamespace

import pytest

import chatsherpa.pool as pool_module
from chatsherpa.base import BaseProvider
from chatsherpa.exceptions import BotChallengeError, BrowserUnavailableError, LoginRequiredError
from chatsherpa.pool import BrowserPool


class _Recorder(BaseProvider):
    name = "scripted"


def _provider(page_text, logged_in=False):
    p = object.__new__(_Recorder)
    p.name = "scripted"
    p._page = SimpleNamespace(url="https://example.test/")

    async def is_logged_in():
        return logged_in

    async def text():
        return page_text

    p.is_logged_in = is_logged_in
    p._page_text = text
    return p


async def test_bot_check_page_is_reported_as_a_challenge_not_a_login_problem():
    with pytest.raises(BotChallengeError):
        await _provider("Just a moment...\nChecking your browser").ensure_logged_in()


async def test_ordinary_logged_out_page_is_still_a_login_problem():
    with pytest.raises(LoginRequiredError):
        await _provider("Log in to continue").ensure_logged_in()


async def test_signed_in_page_passes():
    await _provider("anything", logged_in=True).ensure_logged_in()


class _BrokenPlaywright:
    async def start(self):
        raise RuntimeError("EROFS: read-only file system, mkdtemp")


async def test_browser_start_failure_is_a_browser_unavailable_error(monkeypatch):
    monkeypatch.setattr(pool_module, "async_playwright", lambda: _BrokenPlaywright())
    pool = BrowserPool(headless=False)
    with pytest.raises(BrowserUnavailableError) as info:
        await pool.start()
    assert "EROFS" in str(info.value)
