"""Tests for the framework-agnostic TelegramBridge core (no PTB/aiogram needed)."""

import pytest

from chatsherpa.exceptions import LoginRequiredError
from chatsherpa.integrations import TelegramBridge


class FakeBridge:
    """Stand-in for ChatSherpa: records asks, fakes sessions, can raise."""

    def __init__(self, sessions=(), answer="pong", raise_login=False):
        self._sessions = set(sessions)      # provider names the user "has"
        self.answer = answer
        self.raise_login = raise_login
        self.asks = []                      # (provider, user_id, prompt)

    async def ask(self, provider, user_id, prompt):
        self.asks.append((provider, str(user_id), prompt))
        if self.raise_login:
            raise LoginRequiredError("expired")
        return self.answer

    async def has_session(self, provider, user_id):
        return provider in self._sessions


async def test_plain_message_routes_to_default_provider():
    fake = FakeBridge(answer="hi there")
    tg = TelegramBridge(fake)
    reply = await tg.handle(42, "hello?")
    assert reply == "hi there"
    assert fake.asks == [("chatgpt", "42", "hello?")]


async def test_prefixed_ask_command():
    fake = FakeBridge(answer="42")
    tg = TelegramBridge(fake)
    reply = await tg.handle(7, "/ai what is 6 times 7")
    assert reply == "42"
    assert fake.asks[-1] == ("chatgpt", "7", "what is 6 times 7")


async def test_use_switches_provider_for_subsequent_ask():
    fake = FakeBridge()
    tg = TelegramBridge(fake)
    switched = await tg.handle(1, "/ai_use grok")
    assert "grok" in switched.lower()
    assert tg.provider_for(1) == "grok"
    await tg.handle(1, "ping")
    assert fake.asks[-1][0] == "grok"               # routed to the new default
    # a different user keeps the default
    assert tg.provider_for(2) == "chatgpt"


async def test_status_lists_sessions():
    fake = FakeBridge(sessions={"chatgpt", "grok"})
    tg = TelegramBridge(fake)
    reply = await tg.handle(5, "/ai_status")
    assert "chatgpt" in reply and "grok" in reply and "gemini" in reply
    # a provider with a session is marked, one without is not
    assert "✅ chatgpt" in reply
    assert "— gemini" in reply


async def test_login_required_returns_relogin_hint():
    fake = FakeBridge(raise_login=True)
    tg = TelegramBridge(fake)
    reply = await tg.handle(9, "are you there?")
    assert "expired" in reply.lower()
    assert "/ai_login chatgpt" in reply


async def test_login_local_mode_gives_cli_instructions():
    tg = TelegramBridge(FakeBridge(), login_mode="local")
    reply = await tg.handle(3, "/ai_login grok")
    assert "chatsherpa login" in reply
    assert "grok" in reply


async def test_login_remote_posts_to_server(monkeypatch):
    tg = TelegramBridge(FakeBridge(), login_server_url="https://login.example.com",
                        api_secret="s3cret")
    captured = {}

    def fake_post(body):
        captured["body"] = body
        return {"url": "https://login.example.com/login/tok123", "expires_in": 600}

    monkeypatch.setattr(tg, "_post_login", fake_post)
    reply = await tg.handle(11, "/ai_login gemini")
    assert "https://login.example.com/login/tok123" in reply
    import json
    sent = json.loads(captured["body"])
    # gemini defaults to connect mode (Google can't be driven in an automated browser)
    assert sent == {"provider": "gemini", "user_id": "11", "mode": "connect"}


async def test_command_with_botname_suffix_is_parsed():
    fake = FakeBridge(answer="ok")
    tg = TelegramBridge(fake)
    reply = await tg.handle(1, "/ai_status@MyBot")
    assert "Your sessions" in reply


async def test_custom_prefix_namespacing():
    fake = FakeBridge()
    tg = TelegramBridge(fake, command_prefix="assist")
    assert tg.cmd_login == "assist_login"
    reply = await tg.handle(1, "/assist_use deepseek")
    assert tg.provider_for(1) == "deepseek"
    assert "deepseek" in reply
