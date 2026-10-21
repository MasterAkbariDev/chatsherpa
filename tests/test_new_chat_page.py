"""A chat with no saved URL starts on a fresh page, not the one open on another chat."""

import pytest

from chatsherpa import ChatSherpa, MemorySessionStore


class _RecordingPool:
    def __init__(self):
        self.dropped = []
        self.leased = []

    async def drop(self, user_id, provider):
        self.dropped.append((str(user_id), provider))

    async def lease(self, provider_cls, session, user_id, start_url=None):
        self.leased.append(start_url)
        raise RuntimeError("stop after lease")


async def test_new_named_chat_drops_the_open_page_before_leasing():
    store = MemorySessionStore()
    await store.put("42", "chatgpt", {"cookies": [{"name": "s", "value": "1",
                                                    "domain": "chatgpt.com", "path": "/"}],
                                      "origins": []})
    pool = _RecordingPool()
    bridge = ChatSherpa(store=store, pool=pool)
    with pytest.raises(RuntimeError):
        await bridge.ask("chatgpt", 42, "hi", chat="brand-new")
    assert pool.dropped == [("42", "chatgpt")]
    assert pool.leased == [None]
