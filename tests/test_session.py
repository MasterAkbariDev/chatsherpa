"""Unit tests for session parsing (no browser required)."""

import json

import pytest

from chatsherpa import Session
from chatsherpa.exceptions import SessionError


def test_from_cookie_string():
    s = Session.from_cookie_string("a=1; b=2", "chatgpt.com")
    names = {c["name"]: c["value"] for c in s.cookies}
    assert names == {"a": "1", "b": "2"}
    assert all(c["domain"] == "chatgpt.com" for c in s.cookies)


def test_from_cookie_string_accepts_url_domain():
    s = Session.from_cookie_string("x=y", "https://chat.deepseek.com/path")
    assert s.cookies[0]["domain"] == "chat.deepseek.com"


def test_from_cookies_normalizes_samesite_and_expires():
    s = Session.from_cookies(
        [{"name": "k", "value": "v", "sameSite": "no_restriction", "expirationDate": "123"}],
        default_domain="grok.com",
    )
    c = s.cookies[0]
    assert c["sameSite"] == "None"
    assert c["expires"] == 123.0


def test_coerce_storage_state_dict():
    state = {"cookies": [], "origins": []}
    s = Session.coerce(state)
    assert s.storage_state_arg() == state


def test_coerce_list_is_cookies():
    s = Session.coerce([{"name": "n", "value": "v", "domain": "grok.com"}])
    assert s.cookies[0]["name"] == "n"


def test_coerce_missing_path_raises():
    with pytest.raises(SessionError):
        Session.coerce("/nope/does/not/exist.json")


def test_from_json_file_storage_state(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"cookies": [], "origins": []}))
    s = Session.from_json_file(p)
    assert s.storage_state is not None


def test_from_json_file_cookie_array(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps([{"name": "a", "value": "b", "domain": "x.com"}]))
    s = Session.from_json_file(p)
    assert s.cookies[0]["name"] == "a"


def test_cookies_for_fills_domain_from_url():
    s = Session.from_cookies([{"name": "a", "value": "b"}])
    cookies = s.cookies_for("https://chatgpt.com/")
    assert cookies[0]["domain"] == "chatgpt.com"


def test_empty_session():
    assert Session().is_empty()
