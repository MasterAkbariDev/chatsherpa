"""A 403 from ChatGPT's session check is a bot block, not a login problem (no browser)."""

from types import SimpleNamespace

import pytest

from chatsherpa.exceptions import BotChallengeError, LoginRequiredError
from chatsherpa.providers.chatgpt import ChatGPTProvider


def _provider(status):
    p = object.__new__(ChatGPTProvider)
    p.name = "chatgpt"
    p._page = SimpleNamespace(url="https://chatgpt.com/")

    async def is_logged_in():
        return False

    async def text():
        return "ChatGPT"

    async def session_status():
        return status

    p.is_logged_in = is_logged_in
    p._page_text = text
    p._session_api_status = session_status
    return p


async def test_session_403_is_reported_as_a_bot_block():
    with pytest.raises(BotChallengeError):
        await _provider(403).ensure_logged_in()


async def test_session_200_without_user_is_still_a_login_problem():
    with pytest.raises(LoginRequiredError):
        await _provider(200).ensure_logged_in()
