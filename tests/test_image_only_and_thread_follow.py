"""Two Gemini problems: a reply that is only a picture must finish once the picture loads, and a chat's address must be
saved while its reply is still being written, not only when the reply ends."""

import asyncio
import base64
import time

from chatsherpa.base import BaseProvider, ProviderConfig
from chatsherpa.client import ChatSherpa
from chatsherpa.store import MemorySessionStore

PNG_1x1 = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d5c18a0000000049454e44ae426082"
)).decode()


async def test_an_image_only_reply_finishes_once_its_picture_has_loaded():
    from playwright.async_api import async_playwright

    # A picture of 200 pixels, as a generated image is; a small icon in the same turn must not count on its own.
    html = f"""
    <div class="reply">
      <img alt="icon" width="16" height="16" src="data:image/png;base64,{PNG_1x1}">
      <img alt="cat" width="200" height="200" src="data:image/png;base64,{PNG_1x1}">
    </div>"""

    class _Image(BaseProvider):
        name = "image-only"
        assistant_message_selector = ".reply"
        has_ready_signal = True

        async def _response_ready(self) -> bool:
            return True

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        prov = _Image.attach(page, ProviderConfig(response_timeout_ms=8_000, poll_interval_ms=20,
                                                  ready_settle_ms=20, ready_fallback_ms=60_000))
        started = time.monotonic()
        text = await prov._wait_for_stable_response()
        elapsed = time.monotonic() - started
        await browser.close()
    assert text == ""
    assert elapsed < 4, f"an image-only reply waited {elapsed:.1f}s, as if it had no answer"


async def test_a_chat_address_is_saved_while_its_reply_is_still_written():
    class _Gemini:
        name = "gemini"
        home_url = "https://gemini.example.test/app"

        @classmethod
        def is_conversation(cls, url: str) -> bool:
            return "/app/" in url

    class _Page:
        def __init__(self) -> None:
            self.url = "https://gemini.example.test/app"

    page = _Page()
    bridge = ChatSherpa(store=MemorySessionStore())
    follow = asyncio.ensure_future(bridge._follow_thread(_Gemini, "u1", page, "chat-1", every_s=0.01))
    await asyncio.sleep(0.05)
    assert await bridge.store.get_thread("u1", "gemini", "chat-1") is None

    page.url = "https://gemini.example.test/app/abc123"  # the address appears while the reply is still written
    await asyncio.sleep(0.1)
    assert await bridge.store.get_thread("u1", "gemini", "chat-1") == "https://gemini.example.test/app/abc123"
    follow.cancel()
