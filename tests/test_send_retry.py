"""send() never sends a prompt twice: it waits longer for a late message, then gives up (no browser)."""

from types import SimpleNamespace

from chatsherpa.base import BaseProvider
from chatsherpa.exceptions import ResponseTimeoutError


class _Recorder(BaseProvider):
    name = "scripted"


def _provider(wait_results):
    """A provider whose page steps are recorded; ``wait_results`` drives the confirm waits."""
    p = object.__new__(_Recorder)
    p.name = "scripted"
    p.config = SimpleNamespace(model=None, model_order=None)

    async def logged_in():
        return None

    p.calls = {"fill": 0, "submit": 0, "waits": []}
    results = iter(wait_results)

    async def baseline():
        return 0

    async def fill(text):
        p.calls["fill"] += 1

    async def submit():
        p.calls["submit"] += 1

    async def wait_new(before, timeout_s=None):
        p.calls["waits"].append(timeout_s)
        outcome = next(results)
        if outcome == "timeout":
            raise ResponseTimeoutError("no new assistant message appeared")

    async def stable(**kwargs):
        return "answer"

    async def markdown():
        return "answer"

    p.ensure_logged_in = logged_in
    p._baseline_assistant_count = baseline
    p._fill_input = fill
    p._submit = submit
    p._wait_for_new_message = wait_new
    p._wait_for_stable_response = stable
    p._assistant_markdown = markdown
    return p


async def test_confirmed_send_is_not_resent():
    p = _provider(["ok"])
    assert await p.send("hello") == "answer"
    assert p.calls["fill"] == 1 and p.calls["submit"] == 1
    assert p.calls["waits"] == [p.SEND_CONFIRM_S]


async def test_late_message_is_waited_for_and_never_sent_twice():
    p = _provider(["timeout", "ok"])
    assert await p.send("hello") == "answer"
    assert p.calls["fill"] == 1 and p.calls["submit"] == 1
    assert p.calls["waits"] == [p.SEND_CONFIRM_S, p.SEND_CONFIRM_S * 2]


async def test_message_that_never_registers_is_not_sent_again():
    p = _provider(["timeout", "timeout"])
    raised = False
    try:
        await p.send("hello")
    except ResponseTimeoutError:
        raised = True
    assert raised
    assert p.calls["fill"] == 1 and p.calls["submit"] == 1
