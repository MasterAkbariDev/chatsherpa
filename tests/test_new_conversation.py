"""A chat's provider conversation is compared by its id, so a different account's conversation shows up."""

from chatsherpa.client import _chat_id


def test_same_conversation_has_the_same_id_whatever_the_account_path():
    assert _chat_id("https://gemini.google.com/u/1/app/c19c4b5e20f970d0?pageId=none") == "c19c4b5e20f970d0"
    assert _chat_id("https://gemini.google.com/u/0/app/c19c4b5e20f970d0") == "c19c4b5e20f970d0"


def test_different_conversations_differ():
    assert _chat_id("https://chatgpt.com/c/abc-1") != _chat_id("https://chatgpt.com/c/abc-2")
