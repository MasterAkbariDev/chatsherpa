"""A headed browser on a server with no display gets Xvfb first (no browser)."""

import chatsherpa.pool as pool_module
from chatsherpa.display import VirtualDisplay
from chatsherpa.pool import BrowserPool


class _Chromium:
    def __init__(self, launches):
        self.launches = launches

    async def launch(self, **kwargs):
        self.launches.append(kwargs)
        raise RuntimeError("stop after recording launch arguments")


class _Playwright:
    def __init__(self, launches):
        self.chromium = _Chromium(launches)

    async def stop(self):
        return None


class _Starter:
    def __init__(self, launches):
        self._launches = launches

    async def start(self):
        return _Playwright(self._launches)


async def test_headed_run_starts_the_display_before_launching(monkeypatch):
    launches, started = [], []

    def fake_start(self):
        started.append(True)
        return True

    monkeypatch.setattr(VirtualDisplay, "start_if_needed", fake_start)
    monkeypatch.setattr(pool_module, "async_playwright", lambda: _Starter(launches))
    pool = BrowserPool(headless=False)
    try:
        await pool.start()
    except Exception:
        pass
    assert started, "Xvfb was not started for a headed run"
    assert launches and launches[0]["headless"] is False
