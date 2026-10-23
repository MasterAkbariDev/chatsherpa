"""The login server's browser mode is headless unless CHATSHERPA_HEADLESS turns it off."""

from chatsherpa.cli import headless_from_env


def test_headless_by_default():
    assert headless_from_env({}) is True
    assert headless_from_env({"CHATSHERPA_HEADLESS": "1"}) is True


def test_headed_when_turned_off():
    for value in ("0", "false", "No", " 0 "):
        assert headless_from_env({"CHATSHERPA_HEADLESS": value}) is False
