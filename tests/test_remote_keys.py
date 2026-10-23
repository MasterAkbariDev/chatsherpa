"""Keys from the sign-in viewer reach the remote page as the editing commands Chromium acts on."""

from chatsherpa.login.remote import LiveStreamer


class _FakeCDP:
    def __init__(self):
        self.calls = []

    async def send(self, method, params=None):
        self.calls.append((method, params or {}))


def _streamer():
    s = object.__new__(LiveStreamer)
    s._cdp = _FakeCDP()
    s._stopped = False
    s._meta = {"w": 1280, "h": 720}
    return s


async def test_backspace_deletes_backward_and_is_sent_as_a_key_down_then_up():
    s = _streamer()
    await s.dispatch({"t": "key", "key": "Backspace"})
    down, up = s._cdp.calls
    assert down[0] == "Input.dispatchKeyEvent" and down[1]["type"] == "rawKeyDown"
    assert down[1]["key"] == "Backspace" and down[1]["commands"] == ["deleteBackward"]
    assert up[1]["type"] == "keyUp"


async def test_ctrl_a_selects_all():
    s = _streamer()
    await s.dispatch({"t": "key", "key": "a", "ctrl": True})
    down = s._cdp.calls[0][1]
    assert down["key"] == "a" and down["modifiers"] == 2 and down["commands"] == ["selectAll"]


async def test_shift_arrow_extends_the_selection():
    s = _streamer()
    await s.dispatch({"t": "key", "key": "ArrowLeft", "shift": True})
    assert s._cdp.calls[0][1]["commands"] == ["moveLeftAndModifySelection"]
    assert s._cdp.calls[0][1]["modifiers"] == 8
