"""Tests for the facade, store, events, broker, and login server (no browser)."""

import asyncio

import pytest

from chatsherpa import (
    ChatSherpa, Event, ErrorType, EventBus, EventType, FileSessionStore,
    LoginRequiredError, MemorySessionStore, WebhookSink,
)
from chatsherpa.login.flow import LoginBroker
from chatsherpa.login.remote import LiveStreamer
from chatsherpa.providers import ChatGPTProvider

STATE = {"cookies": [{"name": "s", "value": "1", "domain": "chatgpt.com", "path": "/"}], "origins": []}


async def test_memory_store_roundtrip():
    s = MemorySessionStore()
    assert await s.get("u", "chatgpt") is None
    await s.put("u", "chatgpt", STATE)
    sess = await s.get("u", "chatgpt")
    assert sess and not sess.is_empty()
    assert ("u", "chatgpt") in await s.list()
    await s.delete("u", "chatgpt")
    assert await s.get("u", "chatgpt") is None


async def test_file_store_default_user_path(tmp_path):
    s = FileSessionStore(tmp_path)
    await s.put("local", "chatgpt", STATE)
    assert (tmp_path / "chatgpt.json").exists()      # CLI/scripts compatible
    assert (await s.get("local", "chatgpt")) is not None


async def test_file_store_encrypted_roundtrip(tmp_path):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    s = FileSessionStore(tmp_path, encryption_key=key)
    await s.put("local", "grok", STATE)
    f = tmp_path / "grok.json.enc"
    assert f.exists()
    assert b"cookies" not in f.read_bytes()           # actually encrypted
    assert (await s.get("local", "grok")) is not None


async def test_event_error_payload_maps_type():
    e = Event(EventType.ERROR, provider="chatgpt", user_id=42,
              error_type=ErrorType.LOGIN_REQUIRED, message="x")
    d = e.to_dict()
    assert d["event"] == "error"
    assert d["type"] == "LoginRequired"
    assert d["provider"] == "chatgpt"


async def test_eventbus_fanout_sync_and_async():
    bus = EventBus()
    seen = []
    bus.on(lambda ev: seen.append(("sync", ev.type)))

    async def acb(ev):
        seen.append(("async", ev.type))
    bus.on(acb)
    await bus.emit(Event(EventType.ASK_SUCCEEDED))
    assert ("sync", EventType.ASK_SUCCEEDED) in seen
    assert ("async", EventType.ASK_SUCCEEDED) in seen


async def test_webhook_sink_filters_events(monkeypatch):
    posted = []
    sink = WebhookSink("https://x/y", secret="k")
    monkeypatch.setattr(sink, "_post", lambda body: posted.append(body))
    await sink(Event(EventType.ASK_SUCCEEDED))         # filtered out (not ERROR)
    assert not posted
    await sink(Event(EventType.ERROR, error_type=ErrorType.RESPONSE_TIMEOUT))
    assert posted and b"ResponseTimeout" in posted[0]


async def test_broker_lifecycle():
    b = LoginBroker()
    p = b.create(ChatGPTProvider, "42")
    assert b.get(p.token) is p
    b.resolve(p.token, STATE)
    assert p.handle.status == "success"
    assert (await p.handle.wait(timeout=1)) == STATE
    assert b.get(p.token) is None                       # consumed


async def test_broker_expiry():
    b = LoginBroker()
    p = b.create(ChatGPTProvider, "1", ttl=0)
    await asyncio.sleep(0.01)
    assert b.get(p.token) is None


def test_live_streamer_coord_mapping():
    st = LiveStreamer(None, None, None)
    st._meta = {"w": 800, "h": 600}
    assert st._to_px(0.5, 0.5) == (400.0, 300.0)
    assert st._to_px(0, 1) == (0.0, 600.0)


async def test_ask_without_session_raises_and_emits_error():
    bridge = ChatSherpa(store=MemorySessionStore())
    errors = []
    bridge.on_error(lambda ev: errors.append(ev))
    with pytest.raises(LoginRequiredError):
        await bridge.ask("chatgpt", 7, "hi")
    assert errors and errors[0].error_type == ErrorType.LOGIN_REQUIRED


async def test_login_server_http_layer():
    from aiohttp.test_utils import TestClient, TestServer
    from chatsherpa.login.server import LoginServer
    srv = LoginServer(MemorySessionStore(), "https://login.example.com")
    async with TestClient(TestServer(srv.app())) as client:
        r = await client.get("/healthz")
        assert r.status == 200 and (await r.json())["ok"] is True
        r = await client.get("/login/does-not-exist")
        assert r.status == 404
        r = await client.post("/api/login", json={})   # missing provider
        assert r.status == 400


async def test_connect_ingest_flow():
    from aiohttp.test_utils import TestClient, TestServer
    from chatsherpa.login.server import LoginServer
    store = MemorySessionStore()
    srv = LoginServer(store, "https://login.example.com")
    async with TestClient(TestServer(srv.app())) as client:
        # 1. start a "connect" login (no server browser launched)
        r = await client.post("/api/login", json={"provider": "gemini", "user_id": 9, "mode": "connect"})
        assert r.status == 200
        info = await r.json()
        token = info["token"]
        assert "/connect/" in info["url"]
        # 2. the connect page renders with the token
        r = await client.get(f"/connect/{token}")
        assert r.status == 200 and token in await r.text()
        # 3. ingest the session (as the extension / CLI grab would)
        cookies = [{"name": "SID", "value": "x", "domain": ".google.com", "path": "/"}]
        r = await client.post("/api/ingest", json={"token": token, "cookies": cookies})
        assert r.status == 200 and (await r.json())["ok"] is True
        # 4. it's now stored, and the token is single-use
        assert await store.get("9", "gemini") is not None
        r = await client.post("/api/ingest", json={"token": token, "cookies": cookies})
        assert r.status == 410


def test_busy_message_raises_rate_limited():
    from chatsherpa.providers import GrokProvider
    from chatsherpa import RateLimitedError
    p = GrokProvider()
    with pytest.raises(RateLimitedError):
        p._raise_if_busy("Grok is currently experiencing high demand. Try again later.")
    p._raise_if_busy("ok")  # normal reply: no raise


async def test_ensure_model_picks_first_available():
    from chatsherpa.providers import GrokProvider
    p = GrokProvider()
    tried = []

    async def fake_select(m):
        tried.append(m)
        return m == "Expert"
    p.select_model = fake_select
    chosen = await p.ensure_model(["Fast-missing", "Expert", "Auto"])
    assert chosen == "Expert"
    assert tried == ["Fast-missing", "Expert"]  # stops at first success


def test_telegram_connect_mode_for_google():
    from chatsherpa.integrations import TelegramBridge
    tg = TelegramBridge(bridge=object(), connect_providers=("gemini",), link_mode="live")
    assert tg._mode_for("gemini") == "connect"
    assert tg._mode_for("chatgpt") == "live"


async def test_telegram_handle_routes_ask(monkeypatch):
    from chatsherpa.integrations import TelegramBridge

    class FakeBridge:
        async def ask(self, provider, uid, msg, **kw):
            return f"{provider}:{msg}"
        async def has_session(self, provider, uid):
            return provider == "chatgpt"
    tg = TelegramBridge(FakeBridge())
    assert await tg.handle(1, "/ai hello") == "chatgpt:hello"
    assert tg._do_use("1", "grok").lower().startswith("default")
    assert await tg.handle(1, "plain text") == "grok:plain text"  # uses /use choice


async def test_ingest_rejects_bad_token():
    from aiohttp.test_utils import TestClient, TestServer
    from chatsherpa.login.server import LoginServer
    srv = LoginServer(MemorySessionStore(), "https://login.example.com")
    async with TestClient(TestServer(srv.app())) as client:
        r = await client.post("/api/ingest", json={"token": "nope", "cookies": [{"name": "a", "value": "b", "domain": "x.com"}]})
        assert r.status == 410
