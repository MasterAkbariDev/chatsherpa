import asyncio

from chatsherpa.store import DEFAULT_CHAT, FileSessionStore, MemorySessionStore


def _run(coro):
    return asyncio.run(coro)


def test_named_chats_are_kept_per_user_and_provider(tmp_path):
    store = FileSessionStore(tmp_path)

    async def scenario():
        await store.put_thread("42", "chatgpt", "https://chatgpt.com/c/a", "main")
        await store.put_thread("42", "chatgpt", "https://chatgpt.com/c/b", "billing")
        await store.put_thread("7", "chatgpt", "https://chatgpt.com/c/other", "main")
        assert await store.get_thread("42", "chatgpt", "main") == "https://chatgpt.com/c/a"
        assert await store.get_thread("42", "chatgpt", "billing") == "https://chatgpt.com/c/b"
        assert await store.get_thread("42", "chatgpt", "missing") is None
        assert await store.get_thread("7", "chatgpt", "main") == "https://chatgpt.com/c/other"
        assert await store.list_threads("42", "chatgpt") == {
            "main": "https://chatgpt.com/c/a",
            "billing": "https://chatgpt.com/c/b",
        }

    _run(scenario())


def test_legacy_single_thread_file_reads_as_default_chat(tmp_path):
    store = FileSessionStore(tmp_path)
    (tmp_path / "chatgpt.thread").write_text("https://chatgpt.com/c/old\n", encoding="utf-8")

    async def scenario():
        assert await store.get_thread("local", "chatgpt") == "https://chatgpt.com/c/old"
        assert await store.get_thread("local", "chatgpt", DEFAULT_CHAT) == "https://chatgpt.com/c/old"
        assert await store.list_threads("local", "chatgpt") == {DEFAULT_CHAT: "https://chatgpt.com/c/old"}

    _run(scenario())


def test_chats_file_is_not_listed_as_a_session(tmp_path):
    store = FileSessionStore(tmp_path)

    async def scenario():
        await store.put_thread("42", "grok", "https://grok.com/c/x", "main")
        assert await store.list() == []

    _run(scenario())


def test_memory_store_supports_named_chats():
    store = MemorySessionStore()

    async def scenario():
        await store.put_thread("1", "deepseek", "https://chat.deepseek.com/a/1", "support")
        assert await store.get_thread("1", "deepseek", "support") == "https://chat.deepseek.com/a/1"
        assert await store.get_thread("1", "deepseek") is None
        assert await store.list_threads("1", "deepseek") == {"support": "https://chat.deepseek.com/a/1"}

    _run(scenario())
