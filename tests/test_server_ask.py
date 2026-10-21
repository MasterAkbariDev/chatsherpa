"""Tests for the login server's /api/ask route (no browser, no network)."""

from aiohttp.test_utils import TestClient, TestServer

from chatsherpa.exceptions import LoginRequiredError
from chatsherpa.login.server import LoginServer
from chatsherpa.store import MemorySessionStore


class FakeBridge:
    """Stands in for the warm ChatSherpa bridge the server holds."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []                     # (provider, user_id, prompt, chat)

    async def ask(self, provider, user_id, prompt, model=None, model_order=None,
                  new_chat=False, account=None, on_model=None, chat="default"):
        self.calls.append((provider, user_id, prompt, chat))
        if self.error:
            raise self.error
        if on_model:
            on_model("Test Model")
        return f"echo: {prompt}"

    async def aclose(self):
        return None


async def _client(secret=None, bridge=None):
    server = LoginServer(store=MemorySessionStore(), public_base_url="https://login.example.com",
                         api_secret=secret)
    server.bridge = bridge or FakeBridge()
    client = TestClient(TestServer(server.app()))
    await client.start_server()
    return client, server.bridge


async def test_ask_returns_text_and_model_and_passes_the_chat_name():
    client, bridge = await _client()
    try:
        resp = await client.post("/api/ask", json={"provider": "chatgpt", "prompt": "hi", "chat": "planning"})
        assert resp.status == 200
        assert await resp.json() == {"text": "echo: hi", "model": "Test Model"}
        assert bridge.calls == [("chatgpt", "local", "hi", "planning")]
    finally:
        await client.close()


async def test_ask_requires_the_secret_when_one_is_set():
    client, bridge = await _client(secret="s3cret")
    try:
        denied = await client.post("/api/ask", json={"provider": "chatgpt", "prompt": "hi"})
        assert denied.status == 401
        allowed = await client.post("/api/ask", json={"provider": "chatgpt", "prompt": "hi"},
                                    headers={"X-ChatSherpa-Secret": "s3cret"})
        assert allowed.status == 200
        assert len(bridge.calls) == 1
    finally:
        await client.close()


async def test_ask_rejects_unknown_provider_and_empty_prompt():
    client, bridge = await _client()
    try:
        assert (await client.post("/api/ask", json={"provider": "nope", "prompt": "hi"})).status == 400
        assert (await client.post("/api/ask", json={"provider": "chatgpt", "prompt": "  "})).status == 400
        assert bridge.calls == []
    finally:
        await client.close()


async def test_missing_session_is_reported_as_login_required():
    bridge = FakeBridge(error=LoginRequiredError("no session for user 1"))
    client, _ = await _client(bridge=bridge)
    try:
        resp = await client.post("/api/ask", json={"provider": "chatgpt", "prompt": "hi"})
        assert resp.status == 401
        body = await resp.json()
        assert body["error"] == "login_required"
    finally:
        await client.close()
