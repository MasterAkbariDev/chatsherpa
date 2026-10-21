"""A full chat is reported as ChatFullError, not as a timeout (no browser)."""

from types import SimpleNamespace

from chatsherpa.base import BaseProvider
from chatsherpa.exceptions import ChatFullError, ResponseTimeoutError


class _Recorder(BaseProvider):
    name = "scripted"
    chat_full_markers = ("Chat memory full",)


def _provider(text, wait_outcome="timeout"):
    p = object.__new__(_Recorder)
    p.name = "scripted"
    p.config = SimpleNamespace(model=None, model_order=None)
    p.calls = {"fill": 0, "submit": 0}

    async def logged_in():
        return None

    async def baseline():
        return 0

    async def fill(text):
        p.calls["fill"] += 1

    async def submit():
        p.calls["submit"] += 1

    async def wait_new(before, timeout_s=None):
        if wait_outcome == "timeout":
            await p._raise_if_chat_full()
            raise ResponseTimeoutError("no new assistant message appeared")

    async def page_text():
        return text

    p.ensure_logged_in = logged_in
    p._baseline_assistant_count = baseline
    p._fill_input = fill
    p._submit = submit
    p._wait_for_new_message = wait_new
    p._page_text = page_text
    p._wait_for_stable_response = lambda: _ok()
    p._assistant_markdown = lambda: _ok()
    return p


async def _ok():
    return "answer"


async def test_full_chat_is_refused_before_typing():
    p = _provider("Chat memory full. Start a new chat or upgrade.")
    try:
        await p.send("hello")
    except ChatFullError as exc:
        assert "full" in str(exc).lower()
    else:
        raise AssertionError("expected ChatFullError")
    assert p.calls["fill"] == 0 and p.calls["submit"] == 0


async def test_full_chat_found_at_timeout_is_not_reported_as_a_timeout():
    p = _provider("ordinary page text", wait_outcome="timeout")
    p.chat_full_markers = ("Chat memory full",)
    try:
        await p.send("hello")
    except ResponseTimeoutError:
        pass
    else:
        raise AssertionError("expected ResponseTimeoutError on an ordinary timeout")


async def test_no_markers_means_no_check():
    p = _provider("Chat memory full", wait_outcome="ok")
    p.chat_full_markers = ()
    assert await p.send("hello") == "answer"
    assert p.calls["fill"] == 1
