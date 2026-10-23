"""The sign-in status answer: a known answer comes back at once, a stale one is replaced by a fresh check when it is
quick, and a check that cannot run never turns a known answer into "not connected". The browser is replaced by fakes,
so nothing here starts a browser."""

import asyncio
import time

from chatsherpa.login.server import LoginServer
from chatsherpa.store import MemorySessionStore


def _server(calls, answer=True, delay=0.0):
    server = LoginServer(store=MemorySessionStore(), public_base_url="https://login.example.com")

    async def fake_run(key, provider, user_id):
        calls.append(key)
        await asyncio.sleep(delay)
        server._login_checks[key] = (time.monotonic(), answer)
        return answer

    server._run_login_check = fake_run
    return server


async def test_a_fresh_answer_comes_back_without_a_check():
    calls = []
    server = _server(calls)
    server._login_checks["u:grok"] = (time.monotonic(), True)
    assert await server._check_login("grok", "u") == (True, False)
    await asyncio.sleep(0)
    assert calls == []


async def test_a_stale_answer_is_replaced_by_a_quick_fresh_check():
    calls = []
    server = _server(calls, answer=True)
    server._login_checks["u:gemini"] = (time.monotonic() - 100, False)  # older than the not-connected TTL
    assert await server._check_login("gemini", "u") == (True, False)
    assert calls == ["u:gemini"]
    assert server._refreshing == {}


async def test_a_stale_answer_comes_back_marked_stale_when_the_fresh_check_is_slow():
    calls = []
    server = _server(calls, answer=True, delay=0.5)
    server.LOGIN_CHECK_WAIT_S = 0.05
    server._login_checks["u:gemini"] = (time.monotonic() - 100, False)
    signed_in, stale = await server._check_login("gemini", "u")
    assert (signed_in, stale) == (False, True)
    # The check keeps running, and a second poll does not start another one.
    await server._check_login("gemini", "u")
    assert calls == ["u:gemini"]
    await asyncio.sleep(0.6)
    assert server._login_checks["u:gemini"][1] is True


async def test_with_nothing_known_the_first_answer_waits_for_the_check():
    calls = []
    server = _server(calls, answer=True, delay=0.05)
    assert await server._check_login("chatgpt", "u") == (True, False)
    assert calls == ["u:chatgpt"]


async def test_a_failed_check_keeps_the_last_known_answer():
    server = LoginServer(store=MemorySessionStore(), public_base_url="https://login.example.com")
    stamp = time.monotonic() - 100  # stale, so a check runs
    server._login_checks["u:grok"] = (stamp, True)

    async def cannot_run(provider, user_id):
        raise RuntimeError("browser busy")

    server._browser_check = cannot_run
    assert await server._run_login_check("u:grok", "grok", "u") is True
    assert server._login_checks["u:grok"] == (stamp, True)


async def test_a_failed_check_with_nothing_known_says_unknown_and_records_nothing():
    server = LoginServer(store=MemorySessionStore(), public_base_url="https://login.example.com")

    async def cannot_run(provider, user_id):
        raise TimeoutError()

    server._browser_check = cannot_run
    assert await server._run_login_check("u:grok", "grok", "u") is None
    assert "u:grok" not in server._login_checks


async def test_a_connected_account_that_reads_signed_out_is_checked_once_more():
    server = LoginServer(store=MemorySessionStore(), public_base_url="https://login.example.com")
    server._login_checks["u:grok"] = (time.monotonic() - 100, True)
    reads = iter([False, True])

    async def read_once(provider, user_id):
        return next(reads)

    server._browser_check = read_once
    assert await server._run_login_check("u:grok", "grok", "u") is True
    assert server._login_checks["u:grok"][1] is True


async def test_a_not_connected_answer_is_not_rechecked():
    server = LoginServer(store=MemorySessionStore(), public_base_url="https://login.example.com")
    server._login_checks["u:grok"] = (time.monotonic() - 100, False)
    reads = []

    async def read_once(provider, user_id):
        reads.append(provider)
        return False

    server._browser_check = read_once
    assert await server._run_login_check("u:grok", "grok", "u") is False
    assert reads == ["grok"]


async def test_a_saved_sign_in_replaces_a_cached_not_connected_answer():
    server = LoginServer(store=MemorySessionStore(), public_base_url="https://login.example.com")
    server._login_checks["u:gemini"] = (time.monotonic(), False)
    await server._saved_login("u", "gemini", {"cookies": [], "origins": []})
    assert server._login_checks["u:gemini"][1] is True


async def test_a_page_that_was_not_read_is_not_a_not_signed_in_answer():
    class _Prov:
        challenge_markers = ("Error 403 (Forbidden)",)

        def __init__(self, url, text):
            self.page = type("P", (), {"url": url})()
            self._text = text

        async def _page_text(self):
            return self._text

    server_cls = LoginServer
    assert await server_cls._page_not_read(_Prov("about:blank", "")) is True
    assert await server_cls._page_not_read(_Prov("chrome-error://chromewebdata/", "")) is True
    assert await server_cls._page_not_read(_Prov("https://gemini.google.com/app", "Error 403 (Forbidden)")) is True
    assert await server_cls._page_not_read(_Prov("https://gemini.google.com/app", "Hello")) is False
    # A sign-in page is a real answer: the page was read, and it says signed out.
    assert await server_cls._page_not_read(_Prov("https://accounts.google.com/signin", "Sign in")) is False
