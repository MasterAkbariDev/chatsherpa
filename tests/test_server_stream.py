"""The streamed ask route (NDJSON) and the callbacks ChatSherpa.ask hands to a provider. No browser."""

import asyncio
import json
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from chatsherpa import ChatSherpa, MemorySessionStore
from chatsherpa.exceptions import ChatFullError, LoginRequiredError, ResponseTimeoutError
from chatsherpa.login.server import LoginServer

STATE = {"cookies": [{"name": "s", "value": "1", "domain": "chatgpt.com", "path": "/"}], "origins": []}


class FakeStreamBridge:
    """Replays ``script`` through the callbacks the server passes to ``ask``."""

    def __init__(self, script=(), error=None, final="done text"):
        self.script = list(script)
        self.error = error
        self.final = final
        self.calls = []

    async def ask(self, provider, user_id, prompt, model=None, model_order=None,
                  new_chat=False, account=None, on_model=None, on_media=None, chat="default",
                  on_text=None, on_status=None):
        self.calls.append((provider, prompt, chat))
        if on_model:
            on_model("Test Model")
        for kind, value in self.script:
            if kind == "status":
                on_status(value)
            elif kind == "text":
                on_text(value)
            elif kind == "replace":
                on_text(value, replace=True)
            elif kind == "sleep":
                await asyncio.sleep(value)
        if self.error:
            raise self.error
        return self.final

    async def aclose(self):
        return None


async def _client(bridge, secret=None, heartbeat=None):
    server = LoginServer(store=MemorySessionStore(), public_base_url="https://login.example.com",
                         api_secret=secret)
    server.bridge = bridge
    if heartbeat is not None:
        server.STREAM_HEARTBEAT_S = heartbeat
    client = TestClient(TestServer(server.app()))
    await client.start_server()
    return client


def _lines(raw: str) -> list:
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


async def test_stream_sends_status_text_and_a_final_done_line_in_order():
    bridge = FakeStreamBridge(script=[
        ("status", "Searching the Web"), ("text", "Hel"), ("status", "Working"),
        ("text", "lo"), ("replace", "Help"),
    ], final="Help")
    client = await _client(bridge)
    try:
        resp = await client.post("/api/ask/stream", json={"provider": "chatgpt", "prompt": "hi", "chat": "planning"})
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "application/x-ndjson"
        assert "no-transform" in resp.headers["Cache-Control"]
        events = _lines(await resp.text())
        assert [e["type"] for e in events] == ["status", "status", "text", "status", "text", "text", "done"]
        assert [e["label"] for e in events if e["type"] == "status"] == ["Working", "Searching the Web", "Working"]
        assert all(isinstance(e["elapsedMs"], int) for e in events if e["type"] == "status")
        assert events[2] == {"type": "text", "delta": "Hel"}
        assert events[5] == {"type": "text", "replace": "Help"}
        assert events[-1] == {"type": "done", "text": "Help", "model": "Test Model", "media": []}
        assert bridge.calls == [("chatgpt", "hi", "planning")]
    finally:
        await client.close()


async def test_quiet_provider_gets_status_heartbeats_until_the_reply_ends():
    bridge = FakeStreamBridge(script=[("sleep", 0.2)], final="late")
    client = await _client(bridge, heartbeat=0.03)
    try:
        events = _lines(await (await client.post("/api/ask/stream", json={"provider": "grok", "prompt": "hi"})).text())
        beats = [e for e in events if e["type"] == "status"]
        assert len(beats) >= 4
        assert {e["label"] for e in beats} == {"Working"}
        assert events[-1]["type"] == "done"
    finally:
        await client.close()


async def test_provider_failures_arrive_as_an_error_line_with_the_same_code_as_ask():
    cases = [
        (LoginRequiredError("no session"), 401, "login_required"),
        (ChatFullError("chat full"), 409, "chat_full"),
        (ResponseTimeoutError("no reply"), 504, "timeout"),
        (RuntimeError("selector #composer SECRET-DETAIL"), 500, "failed"),
    ]
    for error, status, code in cases:
        client = await _client(FakeStreamBridge(error=error))
        try:
            resp = await client.post("/api/ask/stream", json={"provider": "deepseek", "prompt": "hi"})
            assert resp.status == 200  # the stream has already started
            events = _lines(await resp.text())
            assert events[-1]["type"] == "error"
            assert events[-1]["status"] == status
            assert events[-1]["error"] == code
            assert not any(e["type"] == "done" for e in events)
        finally:
            await client.close()


async def test_refusals_before_the_stream_are_plain_json_and_never_ask():
    bridge = FakeStreamBridge()
    client = await _client(bridge, secret="s3cret")
    try:
        denied = await client.post("/api/ask/stream", json={"provider": "chatgpt", "prompt": "hi"})
        assert denied.status == 401
        assert await denied.json() == {"error": "unauthorized"}
        allowed_headers = {"X-ChatSherpa-Secret": "s3cret"}
        bad = await client.post("/api/ask/stream", data="not json", headers=allowed_headers)
        assert bad.status == 400
        unknown = await client.post("/api/ask/stream", json={"provider": "nope", "prompt": "hi"}, headers=allowed_headers)
        assert unknown.status == 400
        empty = await client.post("/api/ask/stream", json={"provider": "chatgpt", "prompt": "  "}, headers=allowed_headers)
        assert empty.status == 400
        assert bridge.calls == []
    finally:
        await client.close()


async def test_chatsherpa_ask_passes_the_text_and_status_callbacks_to_the_provider():
    seen = {}

    class Provider:
        page = SimpleNamespace(url="https://chatgpt.com/c/abc")

        async def send(self, prompt, model=None, model_order=None, on_model=None, on_text=None, on_status=None):
            seen["callbacks"] = (on_text is not None, on_status is not None)
            on_status("Working")
            on_text("Hi")
            return "Hi"

    class Lease:
        async def __aenter__(self):
            return Provider()

        async def __aexit__(self, *exc):
            return False

    class Pool:
        async def lease(self, provider_cls, session, uid, start_url=None):
            return Lease()

        async def drop(self, uid, name):
            return None

    store = MemorySessionStore()
    await store.put("u", "chatgpt", STATE)
    texts, statuses = [], []
    bridge = ChatSherpa(store=store, pool=Pool())
    reply = await bridge.ask("chatgpt", "u", "hi", on_text=texts.append, on_status=statuses.append)
    assert reply == "Hi"
    assert seen["callbacks"] == (True, True)
    assert texts == ["Hi"] and statuses == ["Working"]
