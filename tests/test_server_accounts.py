"""Tests for the per-user account routes: /api/models and /api/logout (no browser, no network)."""

from aiohttp.test_utils import TestClient, TestServer

from chatsherpa.exceptions import LoginRequiredError
from chatsherpa.login.server import LoginServer
from chatsherpa.store import MemorySessionStore


class FakePool:
    def __init__(self):
        self.dropped = []

    async def drop(self, user_id, provider):
        self.dropped.append((user_id, provider))


class FakeBridge:
    def __init__(self, store, models=None, error=None):
        self.store = store
        self.pool = FakePool()
        self.models = models if models is not None else ["Fast", "Thinking"]
        self.error = error
        self.model_calls = []

    async def list_models(self, provider, user_id="default", account=None):
        self.model_calls.append((provider, user_id))
        if self.error:
            raise self.error
        return {"models": self.models, "account": None}

    async def has_session(self, provider, user_id="default"):
        s = await self.store.get(user_id, provider)
        return bool(s and not s.is_empty())

    async def aclose(self):
        return None


async def _client(secret="s3cret", bridge=None):
    store = MemorySessionStore()
    server = LoginServer(store=store, public_base_url="https://login.example.com", api_secret=secret)
    server.bridge = bridge or FakeBridge(store)
    client = TestClient(TestServer(server.app()))
    await client.start_server()
    return client, server.bridge


AUTH = {"X-ChatSherpa-Secret": "s3cret"}
STATE = {"cookies": [{"name": "sid", "value": "1", "domain": "x", "path": "/"}], "origins": []}


async def test_models_returns_the_account_menu_for_that_user():
    client, bridge = await _client()
    try:
        resp = await client.get("/api/models", params={"provider": "grok", "user_id": "web-42"}, headers=AUTH)
        assert resp.status == 200
        assert await resp.json() == {"models": ["Fast", "Thinking"]}
        assert bridge.model_calls == [("grok", "web-42")]
    finally:
        await client.close()


async def test_models_maps_missing_login_and_bad_input():
    client, _ = await _client(bridge=FakeBridge(MemorySessionStore(), error=LoginRequiredError("no session")))
    try:
        resp = await client.get("/api/models", params={"provider": "grok", "user_id": "web-1"}, headers=AUTH)
        assert resp.status == 401
        assert (await resp.json())["error"] == "login_required"
        assert (await client.get("/api/models", params={"provider": "nope", "user_id": "x"}, headers=AUTH)).status == 400
        assert (await client.get("/api/models", params={"provider": "grok"}, headers=AUTH)).status == 400
    finally:
        await client.close()


async def test_models_requires_the_secret():
    client, bridge = await _client()
    try:
        resp = await client.get("/api/models", params={"provider": "grok", "user_id": "web-1"})
        assert resp.status == 401
        assert bridge.model_calls == []
    finally:
        await client.close()


async def test_logout_forgets_only_that_users_provider_and_drops_the_warm_browser():
    store = MemorySessionStore()
    bridge = FakeBridge(store)
    await store.put("web-1", "grok", STATE)
    await store.put("web-2", "grok", STATE)
    client, _ = await _client(bridge=bridge)
    try:
        resp = await client.post("/api/logout", json={"provider": "grok", "user_id": "web-1"}, headers=AUTH)
        assert resp.status == 200
        assert await store.get("web-1", "grok") is None
        assert await store.get("web-2", "grok") is not None
        assert bridge.pool.dropped == [("web-1", "grok")]
    finally:
        await client.close()


async def test_logout_rejects_bad_input_and_requires_the_secret():
    client, bridge = await _client()
    try:
        assert (await client.post("/api/logout", json={"provider": "nope", "user_id": "web-1"}, headers=AUTH)).status == 400
        assert (await client.post("/api/logout", json={"provider": "grok", "user_id": "web-1"})).status == 401
        assert bridge.pool.dropped == []
    finally:
        await client.close()
