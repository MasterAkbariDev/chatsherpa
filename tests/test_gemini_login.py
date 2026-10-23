"""Gemini counts as signed in only with Google's account button on the page and no "Sign in" link."""

from chatsherpa.base import ProviderConfig
from chatsherpa.providers.gemini import GeminiProvider


class _Locator:
    def __init__(self, present: bool):
        self._present = present

    async def count(self):
        return 1 if self._present else 0

    @property
    def first(self):
        return self

    async def is_visible(self):
        return self._present


class _Page:
    def __init__(self, composer: bool, account: bool, sign_in: bool):
        self._map = {
            GeminiProvider.input_selector: composer,
            GeminiProvider._SIGNED_IN: account,
            GeminiProvider._SIGN_IN_WALL: sign_in,
        }

    def locator(self, selector):
        return _Locator(self._map.get(selector, False))


class _WithPage(GeminiProvider):
    """The real provider, with a fake page in place of the browser's."""

    def __init__(self, page):
        self.config = ProviderConfig(login_wait_ms=1000)
        self._fake_page = page

    @property
    def page(self):
        return self._fake_page


def _provider(page) -> GeminiProvider:
    return _WithPage(page)


async def test_signed_in_page_with_account_button_counts():
    assert await _provider(_Page(composer=True, account=True, sign_in=False)).is_logged_in() is True


async def test_guest_page_with_composer_and_sign_in_link_does_not_count():
    assert await _provider(_Page(composer=True, account=False, sign_in=True)).is_logged_in() is False


async def test_composer_alone_does_not_count():
    assert await _provider(_Page(composer=True, account=False, sign_in=False)).is_logged_in() is False
