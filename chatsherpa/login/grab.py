"""Grab a session from the user's OWN Chrome (no automation login).

Google and others block sign-in inside automation-controlled browsers. The fix
is not to fight that detection but to let the user log in normally in their real
Chrome, then collect the resulting session:

* ``--cdp http://localhost:9222`` connects to a Chrome the user started with
  ``--remote-debugging-port=9222`` and is already logged into (recommended —
  Google trusts it, nothing is automated during login);
* otherwise a dedicated persistent profile is launched with the installed Chrome
  (``channel="chrome"``) for the user to log into.

Only cookies/origins for the target provider are kept — not the whole browser.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Type
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from ..base import BaseProvider, ProviderConfig

log = logging.getLogger("chatsherpa.login.grab")


def _registrable(host: str) -> str:
    parts = (host or "").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


async def _with_live_local_storage(state: Dict[str, Any], page: Any) -> Dict[str, Any]:
    """Merge the live page origin's localStorage into a storageState dict."""
    try:
        origin = await page.evaluate("() => location.origin")
        items = await page.evaluate(
            "() => Object.keys(localStorage).map(k => ({name: k, value: localStorage.getItem(k)}))"
        )
    except Exception as exc:  # noqa: BLE001 - best effort; cookies still work without it
        log.debug("could not read localStorage: %s", exc)
        return state
    origins = [dict(o) for o in state.get("origins", [])]
    entry = next((o for o in origins if o.get("origin") == origin), None)
    if entry is None:
        entry = {"origin": origin, "localStorage": []}
        origins.append(entry)
    entry["localStorage"] = items
    return {**state, "origins": origins}


def _filter_state(state: Dict[str, Any], home_url: str) -> Dict[str, Any]:
    reg = _registrable(urlparse(home_url).hostname or "")
    cookies = [c for c in state.get("cookies", []) if reg in (c.get("domain") or "")]
    origins = [o for o in state.get("origins", []) if reg in o.get("origin", "")]
    return {"cookies": cookies, "origins": origins}


async def grab_session(
    provider_cls: Type[BaseProvider],
    cdp: Optional[str] = None,
    profile: Optional[str] = None,
    headless: bool = False,
    timeout_s: int = 300,
    config: Optional[ProviderConfig] = None,
    channel: str = "chrome",
) -> Dict[str, Any]:
    config = config or ProviderConfig()
    reg = _registrable(urlparse(provider_cls.home_url).hostname or "")
    pw = await async_playwright().start()
    browser = None
    persistent = None
    page = None
    context = None
    navigate = True
    try:
        if cdp:
            browser = await pw.chromium.connect_over_cdp(cdp)
            # Prefer a context/tab already open on the provider (any window, incl.
            # a separate profile). This is what makes a pre-logged-in session work.
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    if reg and reg in (urlparse(pg.url).hostname or ""):
                        context, page, navigate = ctx, pg, False
                        break
                if page is not None:
                    break
            if page is None:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
        else:
            profile = profile or str(Path.home() / ".chatsherpa" / f"{provider_cls.name}-profile")
            Path(profile).mkdir(parents=True, exist_ok=True)
            persistent = await pw.chromium.launch_persistent_context(
                profile, channel=channel, headless=headless)
            context = persistent
            page = context.pages[0] if context.pages else await context.new_page()

        if navigate:
            await page.goto(provider_cls.home_url, wait_until="domcontentloaded",
                            timeout=config.nav_timeout_ms)
        prov = provider_cls.attach(page, config)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if await prov.is_logged_in():
                state = await context.storage_state()
                # storage_state() can miss localStorage for CDP-attached contexts,
                # but many providers keep their auth token there (e.g. DeepSeek's
                # userToken). Read the live page's localStorage and add it.
                state = await _with_live_local_storage(state, page)
                return _filter_state(state, provider_cls.home_url)
            await asyncio_sleep(1.5)
        raise TimeoutError(f"[{provider_cls.name}] not logged in within {timeout_s}s")
    finally:
        try:
            if navigate and page is not None:
                await page.close()   # only close a tab we opened, never the user's
        except Exception:
            pass
        if persistent is not None:
            await persistent.close()
        if browser is not None:
            await browser.close()  # detaches CDP; does not kill the user's Edge
        await pw.stop()


async def asyncio_sleep(s: float) -> None:
    import asyncio
    await asyncio.sleep(s)


def upload_session(ingest_url: str, token: str, provider: str, state: Dict[str, Any],
                   timeout: float = 15.0) -> None:
    """POST a grabbed session to a running login server's /api/ingest."""
    body = json.dumps({"token": token, "provider": provider, "storage_state": state}).encode()
    req = urllib.request.Request(ingest_url, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=timeout).close()
