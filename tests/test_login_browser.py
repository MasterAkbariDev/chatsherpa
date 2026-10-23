"""The sign-in browser uses the same proxy and launch options as the provider pool (no browser started)."""

from chatsherpa.base import ProviderConfig
from chatsherpa.login import server as login_server


class _Chromium:
    def __init__(self):
        self.kwargs = None

    async def launch(self, **kwargs):
        self.kwargs = kwargs
        return object()


class _Playwright:
    def __init__(self):
        self.chromium = _Chromium()


class _Starter:
    def __init__(self):
        self.pw = _Playwright()

    async def start(self):
        return self.pw


async def test_sign_in_browser_goes_through_the_configured_proxy(monkeypatch):
    starter = _Starter()
    monkeypatch.setattr(login_server, "async_playwright", lambda: starter)
    config = ProviderConfig(proxy={"server": "http://127.0.0.1:10808"}, launch_args=["--test-flag"])
    server = login_server.LoginServer(store=None, public_base_url="https://example.test", config=config)
    await server._ensure_browser()
    kwargs = starter.pw.chromium.kwargs
    assert kwargs["proxy"] == {"server": "http://127.0.0.1:10808"}
    assert "--test-flag" in kwargs["args"]
    assert "--disable-dev-shm-usage" in kwargs["args"]
