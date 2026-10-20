"""Tests for the facade, store, events, broker, and login server (no browser)."""

import asyncio
import json

import pytest

from chatsherpa import (
    ChatSherpa, Event, ErrorType, EventBus, EventType, FileSessionStore,
    LoginRequiredError, MemorySessionStore, WebhookSink,
)
from chatsherpa.config import app_home
from chatsherpa.login.flow import LoginBroker
from chatsherpa.login.remote import LiveStreamer
from chatsherpa.providers import ChatGPTProvider

STATE = {"cookies": [{"name": "s", "value": "1", "domain": "chatgpt.com", "path": "/"}], "origins": []}


def test_model_labels_come_from_the_page():
    from chatsherpa.base import clean_model_labels
    from chatsherpa.providers import ChatGPTProvider, DeepSeekProvider, GeminiProvider, GrokProvider
    assert clean_model_labels([
        "3.1 Pro", "3.1 Pro", "Upgrade", "Get Pro", "Model: Expert", "", "Auto",
    ]) == ["3.1 Pro", "Expert", "Auto"]
    for cls in (ChatGPTProvider, DeepSeekProvider, GeminiProvider, GrokProvider):
        assert not getattr(cls, "models", None)


def test_conversation_urls():
    from chatsherpa.providers import ChatGPTProvider, DeepSeekProvider, GeminiProvider, GrokProvider
    assert ChatGPTProvider.is_conversation("https://chatgpt.com/c/abc")
    assert not ChatGPTProvider.is_conversation("https://chatgpt.com/")
    assert GeminiProvider.is_conversation("https://gemini.google.com/app/abc123")
    assert GeminiProvider.is_conversation("https://gemini.google.com/u/1/app/abc123")
    assert not GeminiProvider.is_conversation("https://gemini.google.com/app")
    assert not GeminiProvider.is_conversation("https://gemini.google.com/u/1/app")
    assert GeminiProvider.entry_url("https://gemini.google.com/u/1/app")
    assert GeminiProvider.account_index("https://gemini.google.com/u/1/app") == 1
    assert GeminiProvider.account_index("https://gemini.google.com/app") == 0
    assert GeminiProvider.account_home(1) == "https://gemini.google.com/u/1/app"
    assert DeepSeekProvider.is_conversation("https://chat.deepseek.com/a/chat/s/abc")
    assert not DeepSeekProvider.is_conversation("https://chat.deepseek.com/")
    assert GrokProvider.is_conversation("https://grok.com/chat/abc")
    assert not GrokProvider.is_conversation("https://grok.com/")


async def test_thread_url_roundtrip(tmp_path):
    s = FileSessionStore(tmp_path)
    assert await s.get_thread("local", "chatgpt") is None
    await s.put_thread("local", "chatgpt", "https://chatgpt.com/c/abc")
    assert await s.get_thread("local", "chatgpt") == "https://chatgpt.com/c/abc"
    await s.delete("local", "chatgpt")
    assert await s.get_thread("local", "chatgpt") is None


def test_app_home_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHATSHERPA_HOME", str(tmp_path))
    assert app_home() == tmp_path.resolve()


def test_request_login_link_posts_secret(monkeypatch):
    from chatsherpa.cli import request_login_link
    captured = {}

    class Resp:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"url":"https://login.example/connect/abc","token":"abc","expires_in":600}'

    def fake_open(req, timeout=20):
        captured["url"] = req.full_url
        captured["secret"] = req.get_header("X-chatsherpa-secret")
        captured["body"] = json.loads(req.data.decode())
        return Resp()

    monkeypatch.setattr("chatsherpa.cli.urllib.request.urlopen", fake_open)
    info = request_login_link("https://login.example/", "s3cret", "chatgpt", "local", "connect")
    assert info["url"].endswith("/connect/abc")
    assert captured["url"] == "https://login.example/api/login"
    assert captured["secret"] == "s3cret"
    assert captured["body"] == {"provider": "chatgpt", "user_id": "local", "mode": "connect"}


def test_app_home_uses_install_tree(monkeypatch):
    monkeypatch.delenv("CHATSHERPA_HOME", raising=False)
    root = app_home()
    assert (root / "pyproject.toml").exists()
    assert (root / "chatsherpa").is_dir()


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
