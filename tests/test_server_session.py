"""Tests for the login server's /api/session route (a store lookup: no browser, no network)."""

from aiohttp.test_utils import TestClient, TestServer

from chatsherpa.login.server import LoginServer
from chatsherpa.store import MemorySessionStore

COOKIE_VALUE = "cookie-value-that-must-not-leak"
SIGNED_IN_STATE = {
    "cookies": [{"name": "session", "value": COOKIE_VALUE, "domain": ".chatgpt.com", "path": "/"}],
    "origins": [],
}


async def _client(secret=None, store=None):
    server = LoginServer(store=store or MemorySessionStore(), public_base_url="https://login.example.com",
                         api_secret=secret)
    client = TestClient(TestServer(server.app()))
    await client.start_server()
    return client, server


async def test_session_requires_the_secret_when_one_is_set():
    client, _ = await _client(secret="s3cret")
    try:
        denied = await client.post("/api/session", json={"provider": "chatgpt", "user_id": "1"})
        assert denied.status == 401
        assert await denied.json() == {"error": "unauthorized"}
        allowed = await client.post("/api/session", json={"provider": "chatgpt", "user_id": "1"},
                                    headers={"X-ChatSherpa-Secret": "s3cret"})
        assert allowed.status == 200
    finally:
        await client.close()


async def test_session_rejects_bad_json():
    client, _ = await _client()
    try:
        resp = await client.post("/api/session", data="{not json",
                                 headers={"Content-Type": "application/json"})
        assert resp.status == 400
        assert await resp.json() == {"error": "bad json"}
    finally:
        await client.close()


async def test_session_rejects_unknown_provider():
    client, _ = await _client()
    try:
        resp = await client.post("/api/session", json={"provider": "nope", "user_id": "1"})
        assert resp.status == 400
        assert await resp.json() == {"error": "unknown provider"}
    finally:
        await client.close()


async def test_session_reports_signed_in_when_a_session_is_stored():
    store = MemorySessionStore()
    await store.put("42", "chatgpt", SIGNED_IN_STATE)
    client, _ = await _client(store=store)
    try:
        resp = await client.post("/api/session", json={"provider": "ChatGPT", "user_id": "42"})
        assert resp.status == 200
        body = await resp.json()
        assert body == {"provider": "chatgpt", "signed_in": True}
        # Only the boolean leaves the server: no cookie or storage contents in the reply.
        assert COOKIE_VALUE not in await resp.text()
    finally:
        await client.close()


async def test_session_reports_signed_out_when_none_is_stored():
    client, _ = await _client()
    try:
        resp = await client.post("/api/session", json={"provider": "grok", "user_id": "42"})
        assert resp.status == 200
        assert await resp.json() == {"provider": "grok", "signed_in": False}
    finally:
        await client.close()


async def test_session_agrees_with_the_library_has_session():
    store = MemorySessionStore()
    await store.put("42", "gemini", SIGNED_IN_STATE)
    client, server = await _client(store=store)
    try:
        for user_id in ("42", "43"):
            resp = await client.post("/api/session", json={"provider": "gemini", "user_id": user_id})
            expected = await server.bridge.has_session("gemini", user_id)
            assert (await resp.json())["signed_in"] is expected
    finally:
        await client.close()
