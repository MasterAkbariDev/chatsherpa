"""A reply is handed out while it is written: text as Markdown deltas, status labels, and never reasoning."""

from chatsherpa.base import BaseProvider, ProviderConfig


class _Scripted(BaseProvider):
    name = "scripted-stream"
    has_ready_signal = True


# One step per poll: (plain turn text, answer Markdown, search indicator visible).
# The plain turn text can hold the provider's reasoning; the answer Markdown never does.
STEPS = [
    ("Thinking hard", "", True),
    ("Thinking hard about it", "", True),
    ("Thinking hard about itHel", "Hel", False),
    ("Thinking hard about itHello", "Hello", False),
    ("Thinking hard about itHello, wor", "Hello, wor", False),
    ("Thinking hard about itHello, world", "Hello, world", False),
]


def _scripted(steps):
    """A provider whose page replays ``steps``: poll k reads step k, then holds the last step.

    Within one poll the wait loop reads the status first, then the text, then the Markdown,
    so each reads the step for that same poll (``polls`` counts the text reads so far).
    """
    p = object.__new__(_Scripted)
    p.config = ProviderConfig(poll_interval_ms=1, ready_settle_ms=1, ready_fallback_ms=60_000)
    p.polls = 0
    last = len(steps) - 1

    async def assistant_text():
        index = p.polls
        p.polls += 1
        return steps[min(index, last)][0]

    async def assistant_markdown(text_fallback=True):
        turn, md, _ = steps[min(p.polls - 1, last)]
        return md if md else (turn if text_fallback else "")

    async def response_ready():
        return p.polls - 1 >= len(steps)

    async def status_label():
        return "Searching the Web" if steps[min(p.polls, last)][2] else None

    p._assistant_text = assistant_text
    p._assistant_markdown = assistant_markdown
    p._response_ready = response_ready
    p._status_label = status_label
    return p


def _apply(events):
    """What a browser shows after receiving ``events`` in order."""
    shown = ""
    for kind, value in events:
        shown = value if kind == "replace" else shown + value
    return shown


async def test_text_arrives_as_deltas_and_never_includes_reasoning():
    p = _scripted(STEPS)
    events = []
    await p._wait_for_stable_response(
        on_text=lambda delta, replace=False: events.append(("replace" if replace else "delta", delta)),
    )
    assert events == [("delta", "Hel"), ("delta", "lo"), ("delta", ", wor"), ("delta", "ld")]
    assert _apply(events) == "Hello, world"
    assert all("Thinking" not in value for _, value in events)


async def test_text_that_stops_being_an_append_is_sent_as_a_replacement():
    steps = [("a", "Hello", False), ("b", "Help", False), ("c", "Help me", False)]
    p = _scripted(steps)
    events = []
    await p._wait_for_stable_response(
        on_text=lambda delta, replace=False: events.append(("replace" if replace else "delta", delta)),
    )
    assert events == [("delta", "Hello"), ("replace", "Help"), ("delta", " me")]
    assert _apply(events) == "Help me"


async def test_status_reports_searching_then_working_once_per_change():
    p = _scripted(STEPS)
    labels = []
    await p._wait_for_stable_response(on_status=labels.append)
    assert labels == ["Searching the Web", "Working"]


async def test_without_callbacks_the_wait_is_unchanged():
    p = _scripted(STEPS)
    text = await p._wait_for_stable_response()
    assert text == "Thinking hard about itHello, world"


async def test_status_label_is_none_without_a_verified_search_selector():
    # No provider has a verified "searching" selector yet, so nothing claims a search.
    assert BaseProvider.searching_selector is None
    p = object.__new__(_Scripted)
    p.searching_selector = None
    assert await BaseProvider._status_label(p) is None


async def test_reasoning_blocks_are_left_out_of_the_markdown():
    from playwright.async_api import async_playwright

    html = """
    <div class="reply" id="reply">
      <div class="ds-think-content"><p>PRIVATE REASONING</p></div>
      <div data-testid="thought-block"><p>MORE PRIVATE</p></div>
      <p>The answer.</p>
    </div>
    <div class="thinking-markdown" id="odd"><p>Answer kept</p></div>"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        reply = await page.evaluate(BaseProvider._MD_JS, await page.query_selector("#reply"))
        odd = await page.evaluate(BaseProvider._MD_JS, await page.query_selector("#odd"))
        await browser.close()
    assert reply == "The answer."
    # The reply's own root is never dropped, even if its class looks like reasoning.
    assert odd == "Answer kept"
