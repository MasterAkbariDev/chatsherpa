"""A reply's pictures stay where the text puts them: text, picture, text, picture, picture, text. A picture that cannot
be read takes its marker with it."""

import base64

from chatsherpa.base import BaseProvider, ProviderConfig

PNG_1x1 = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d5c18a0000000049454e44ae426082"
)).decode()
PIC = f'<img width="200" height="200" src="data:image/png;base64,{PNG_1x1}">'


class _Gemini(BaseProvider):
    name = "reply-order"
    media_selector = ".model-response"
    assistant_message_selector = ".model-response-text"
    MEDIA_WAIT_S = 0.5


async def _read(html: str):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        prov = _Gemini.attach(page, ProviderConfig(headless=True))
        markdown = await prov._assistant_markdown()
        media = await prov._capture_media()
        await browser.close()
    from chatsherpa.base import _renumber_pictures
    return _renumber_pictures(markdown, media), media


async def test_pictures_stay_between_the_text_around_them():
    html = f"""
    <div class="model-response">
      <div class="model-response-text"><p>First part.</p></div>
      <div class="generated-image">{PIC}</div>
      <div class="model-response-text"><p>Second part.</p></div>
      <div class="generated-image">{PIC}{PIC}</div>
      <div class="model-response-text"><p>Last part.</p></div>
    </div>"""
    text, media = await _read(html)
    assert text == (
        "First part.\n\n![picture](chatsherpa-picture:0)\n\nSecond part.\n\n"
        "![picture](chatsherpa-picture:1)\n\n![picture](chatsherpa-picture:2)\n\nLast part."
    )
    assert len(media) == 3
    assert all(m["mime"] == "image/png" for m in media)


async def test_a_picture_that_cannot_be_read_takes_its_marker_with_it():
    broken = '<img width="200" height="200" src="http://127.0.0.1:9/missing.png">'
    html = f"""
    <div class="model-response">
      <div class="model-response-text"><p>Before.</p></div>
      <div class="generated-image">{broken}</div>
      <div class="model-response-text"><p>After.</p></div>
      <div class="generated-image">{PIC}</div>
    </div>"""
    text, media = await _read(html)
    assert text == "Before.\n\nAfter.\n\n![picture](chatsherpa-picture:0)"
    assert len(media) == 1


async def test_a_video_is_kept_in_its_place_with_its_picture_marker():
    # A video is a page element with a link to its file (here a data link); it takes its place in the reply's order.
    from playwright.async_api import async_playwright
    import base64 as b64
    mp4_like = b64.b64encode(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64).decode()
    html = f"""
    <div class="model-response">
      <div class="model-response-text"><p>Here is the video.</p></div>
      <div class="generated-video"><video src="data:video/mp4;base64,{mp4_like}"></video></div>
      <div class="model-response-text"><p>Enjoy.</p></div>
    </div>"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        prov = _Gemini.attach(page, ProviderConfig(headless=True))
        markdown = await prov._assistant_markdown()
        await browser.close()
    assert markdown == "Here is the video.\n\n![picture](chatsherpa-picture:0)\n\nEnjoy."


async def test_stopping_presses_the_providers_stop_button_when_it_is_shown():
    from playwright.async_api import async_playwright

    html = '<button data-testid="stop-button" onclick="window.pressed = true">Stop</button>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        prov = _Gemini.attach(page, ProviderConfig(headless=True))
        assert await prov.stop_generating() is True
        assert await page.evaluate("window.pressed === true") is True
        await page.set_content("<p>No reply is being written.</p>")
        assert await prov.stop_generating() is False
        await browser.close()


async def test_a_picture_from_another_site_is_fetched_with_the_browsers_session():
    # The picture is served from another address (another origin), so the page cannot read its pixels; the browser's
    # own request, with its session, still gets it.
    import http.server
    import threading
    from playwright.async_api import async_playwright

    png = base64.b64decode(PNG_1x1)

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/cat.png"
    html = f"""
    <div class="model-response">
      <div class="model-response-text"><p>Here.</p></div>
      <div class="generated-image"><img width="200" height="200" src="{url}"></div>
    </div>"""
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html)
            prov = _Gemini.attach(page, ProviderConfig(headless=True))
            await prov._assistant_markdown()
            media = await prov._capture_media()
            await browser.close()
    finally:
        server.shutdown()
    assert [m["mime"] for m in media] == ["image/png"]
    assert base64.b64decode(media[0]["data"])[:4] == b"\x89PNG"


async def test_a_picture_inside_a_button_keeps_its_place():
    # Gemini shows a picture inside a button; the button is kept, because the picture is part of the reply.
    html = f"""
    <div class="model-response">
      <div class="model-response-text"><p>Here is the photo:</p>
        <button class="image-button"><img width="200" height="200" src="data:image/png;base64,{PNG_1x1}"></button>
      </div>
      <div class="model-response-text"><p>Enjoy.</p></div>
    </div>"""
    text, media = await _read(html)
    assert text == "Here is the photo:\n\n![picture](chatsherpa-picture:0)\n\nEnjoy."
    assert len(media) == 1


async def test_a_picture_drawn_after_the_text_is_in_its_place():
    # The text is complete before the picture is drawn (Gemini draws the picture last). Capturing the pictures first
    # and then reading the text puts the picture's marker where it belongs.
    from playwright.async_api import async_playwright

    html = """
    <div class="model-response">
      <div class="model-response-text"><p>Here is the photo:</p></div>
      <div class="generated-image"></div>
      <div class="model-response-text"><p>Enjoy.</p></div>
    </div>
    <script>
      setTimeout(() => {
        document.querySelector('.generated-image').innerHTML = '<img width="200" height="200" src="data:image/png;base64,%s">';
      }, 1500);
    </script>""" % PNG_1x1
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        prov = _Gemini.attach(page, ProviderConfig(headless=True))
        media = await prov._capture_media(expect_picture=True)
        from chatsherpa.base import _renumber_pictures
        markdown = await prov._assistant_markdown()
        await browser.close()
    text = _renumber_pictures(markdown, media)
    assert text == "Here is the photo:\n\n![picture](chatsherpa-picture:0)\n\nEnjoy."
