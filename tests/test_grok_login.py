"""Grok's logged-in check: the composer alone is not a sign-in (logged-out pages show it too)."""

import asyncio

from chatsherpa.base import ProviderConfig
from chatsherpa.providers.grok import GrokProvider


class FakeLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    async def count(self):
        return 1 if self.page.visible.get(self.selector, 0) else 0

    @property
    def first(self):
        return self

    async def is_visible(self):
        return bool(self.page.visible.get(self.selector, 0))


class FakePage:
    """`visible` maps a selector to a visibility timeline: a value, or a function of elapsed time."""

    def __init__(self, visible):
        self.visible = visible
        self.context = None  # attach() reads it; the check never uses it

    def locator(self, selector):
        return FakeLocator(self, selector)


COMPOSER = GrokProvider.input_selector
WALL = GrokProvider.login_wall_selectors[0]


def provider(page, wait_ms=500):
    return GrokProvider.attach(page, ProviderConfig(headless=True, login_wait_ms=wait_ms))


async def test_composer_without_wall_is_logged_in():
    page = FakePage({COMPOSER: 1})
    assert await provider(page).is_logged_in() is True


async def test_composer_with_persistent_wall_is_logged_out():
    # Logged-out Grok: the composer and the "Sign in" link both stay visible.
    page = FakePage({COMPOSER: 1, WALL: 1})
    assert await provider(page).is_logged_in() is False


async def test_wall_that_clears_during_hydration_counts_as_logged_in():
    # The header shows a sign-in link briefly, then the avatar replaces it.
    page = FakePage({COMPOSER: 1, WALL: 1})

    async def clear_wall():
        await asyncio.sleep(1.5)
        page.visible[WALL] = 0

    task = asyncio.create_task(clear_wall())
    assert await provider(page).is_logged_in() is True
    await task


async def test_composer_that_appears_late_is_logged_in():
    page = FakePage({})

    async def show_composer():
        await asyncio.sleep(1.0)
        page.visible[COMPOSER] = 1

    task = asyncio.create_task(show_composer())
    assert await provider(page, wait_ms=60000).is_logged_in() is True
    await task
