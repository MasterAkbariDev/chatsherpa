"""The sign-in status answer: a known answer comes back at once, and a stale one is refreshed in the background.
A browser check is replaced by a fake, so nothing here starts a browser."""

import asyncio
import time

from chatsherpa.login.server import LoginServer
from chatsherpa.store import MemorySessionStore


def _server(calls, answer=True):
    server = LoginServer(store=MemorySessionStore(), public_base_url="https://login.example.com")

    async def fake_run(key, provider, user_id):
        calls.append(key)
        server._login_checks[key] = (time.monotonic(), answer)
        return answer

    server._run_login_check = fake_run
    return server


async def test_a_fresh_answer_comes_back_without_a_check():
    calls = []
    server = _server(calls)
    server._login_checks["u:grok"] = (time.monotonic(), True)
    assert await server._check_login("grok", "u") is True
    await asyncio.sleep(0)
    assert calls == []


async def test_a_stale_answer_comes_back_at_once_and_is_refreshed_once():
    calls = []
    server = _server(calls, answer=True)
    server._login_checks["u:gemini"] = (time.monotonic() - 100, False)  # older than the 15 second not-connected TTL

    assert await server._check_login("gemini", "u") is False  # the last answer, without waiting
    assert await server._check_login("gemini", "u") is False  # a second poll does not start a second check
    await asyncio.sleep(0)
    assert calls == ["u:gemini"]
    assert server._refreshing == set()
    assert server._login_checks["u:gemini"][1] is True


async def test_with_nothing_known_the_first_answer_waits_for_the_check():
    calls = []
    server = _server(calls, answer=True)
    assert await server._check_login("chatgpt", "u") is True
    assert calls == ["u:chatgpt"]
