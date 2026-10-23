"""A picture that is asked for can appear after the reply's text: the capture waits for it, and an ordinary reply
without pictures does not wait."""

import base64

from chatsherpa.base import BaseProvider, ProviderConfig, _PICTURE_REQUEST

PNG_1x1 = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d5c18a0000000049454e44ae426082"
)).decode()


def test_only_requests_for_a_picture_are_recognised():
    assert _PICTURE_REQUEST.search("generate an AI image of a cat")
    assert _PICTURE_REQUEST.search("عکس یک گربه بکش")
    assert not _PICTURE_REQUEST.search("what is two plus two")


async def _capture(html: str, expect_picture: bool):
    from playwright.async_api import async_playwright

    class _Provider(BaseProvider):
        name = "late-picture"
        media_selector = ".reply"
        assistant_message_selector = ".reply"
        IMAGE_APPEAR_WAIT_S = 15.0
        MEDIA_WAIT_S = 0.5

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        prov = _Provider.attach(page, ProviderConfig(headless=True))
        found = await prov._capture_media(expect_picture=expect_picture)
        await browser.close()
    return found


async def test_a_requested_picture_is_captured_even_when_it_appears_after_the_text():
    html = f"""
    <div class="reply"><p>Here is your image.</p></div>
    <script>
      setTimeout(() => {{
        const img = document.createElement('img');
        img.width = 200; img.height = 200;
        img.src = 'data:image/png;base64,{PNG_1x1}';
        document.querySelector('.reply').appendChild(img);
      }}, 2000);
    </script>"""
    found = await _capture(html, expect_picture=True)
    assert [m["mime"] for m in found] == ["image/png"]


async def test_a_reply_without_a_requested_picture_does_not_wait_for_one():
    html = '<div class="reply"><p>Two plus two is four.</p></div>'
    assert await _capture(html, expect_picture=False) == []
