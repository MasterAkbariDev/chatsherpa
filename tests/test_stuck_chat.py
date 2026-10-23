"""A saved chat that never answers is reported as stuck; a new chat is still a timeout."""

import pytest

from chatsherpa import ChatSherpa, MemorySessionStore
from chatsherpa.exceptions import ChatFullError, ResponseTimeoutError


class _Prov:
    async def send(self, prompt, **kwargs):
        raise ResponseTimeoutError("[chatgpt] no new assistant message appeared (page: x).")


class _Lease:
    async def __aenter__(self):
        return _Prov()

    async def __aexit__(self, *exc):
        return False


class _Pool:
    async def drop(self, user_id, provider):
        return None

    async def lease(self, provider_cls, session, user_id, start_url=None):
        return _Lease()


async def _bridge(with_saved_chat):
    store = MemorySessionStore()
    await store.put("42", "chatgpt", {"cookies": [{"name": "s", "value": "1",
                                                    "domain": "chatgpt.com", "path": "/"}],
                                      "origins": []})
    if with_saved_chat:
        await store.put_thread("42", "chatgpt", "https://chatgpt.com/c/abc", "main")
    return ChatSherpa(store=store, pool=_Pool())


async def test_saved_chat_without_reply_is_reported_as_stuck():
    bridge = await _bridge(with_saved_chat=True)
    with pytest.raises(ChatFullError) as info:
        await bridge.ask("chatgpt", 42, "hi", chat="main")
    assert "stopped answering" in str(info.value)


async def test_new_chat_timeout_stays_a_timeout():
    bridge = await _bridge(with_saved_chat=False)
    with pytest.raises(ResponseTimeoutError):
        await bridge.ask("chatgpt", 42, "hi", chat="brand-new")
