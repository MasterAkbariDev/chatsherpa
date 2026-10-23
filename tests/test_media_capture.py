"""Images a model returns are captured through the page, as bytes, and nothing else is kept."""

import base64

from chatsherpa.base import BaseProvider

PNG_1x1 = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d5c18a0000000049454e44ae426082"
)).decode()


async def test_images_in_a_reply_come_back_as_bytes_and_other_content_is_ignored():
    from playwright.async_api import async_playwright

    html = f"""
    <div class="reply">
      <p>Here is the chart.</p>
      <img alt="chart" width="200" height="200" src="data:image/png;base64,{PNG_1x1}">
      <img alt="icon" width="16" height="16" src="data:image/png;base64,{PNG_1x1}">
      <img alt="svg" width="200" height="200" src="data:image/svg+xml;utf8,&lt;svg/&gt;">
    </div>"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        found = await page.evaluate(
            BaseProvider._MEDIA_JS,
            {"el": await page.query_selector(".reply"), "count": 4, "bytes": 2 * 1024 * 1024},
        )
        await browser.close()
    # The icon is too small and the SVG is not a kept type, so only the chart remains.
    assert [(m["mime"], m["alt"]) for m in found] == [("image/png", "chart")]
    assert base64.b64decode(found[0]["data"])[:4] == b"\x89PNG"


async def test_a_picture_still_loading_is_waited_for():
    import asyncio
    from playwright.async_api import async_playwright

    html = """
    <div class="reply">
      <p>Here is the cat.</p>
      <img alt="cat" width="200" height="200" src="http://slow.test/cat.png">
    </div>"""

    async def slow(route):
        await asyncio.sleep(2.0)
        await route.fulfill(status=200, content_type="image/png", body=base64.b64decode(PNG_1x1))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("http://slow.test/cat.png", slow)
        await page.set_content(html)

        class _Provider(BaseProvider):
            name = "media-wait"
            assistant_message_selector = ".reply"
            MEDIA_WAIT_S = 20.0

            @property
            def page(self):
                return page

        found = await _Provider()._capture_media()
        await browser.close()
    assert [(m["mime"], m["alt"]) for m in found] == [("image/png", "cat")]
