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


async def test_ctrl_backspace_deletes_a_word():
    s = _streamer()
    await s.dispatch({"t": "key", "key": "Backspace", "ctrl": True})
    assert s._cdp.calls[0][1]["commands"] == ["deleteWordBackward"]


async def test_ctrl_shift_arrow_extends_by_word():
    s = _streamer()
    await s.dispatch({"t": "key", "key": "ArrowRight", "ctrl": True, "shift": True})
    assert s._cdp.calls[0][1]["commands"] == ["moveWordRightAndModifySelection"]


async def test_ctrl_home_goes_to_the_start_of_the_document():
    s = _streamer()
    await s.dispatch({"t": "key", "key": "Home", "ctrl": True})
    assert s._cdp.calls[0][1]["commands"] == ["moveToBeginningOfDocument"]


async def test_page_keys_scroll_and_ctrl_shift_z_redoes():
    s = _streamer()
    await s.dispatch({"t": "key", "key": "PageDown"})
    await s.dispatch({"t": "key", "key": "z", "ctrl": True, "shift": True})
    assert s._cdp.calls[0][1]["commands"] == ["scrollPageForward"]
    assert s._cdp.calls[2][1]["commands"] == ["redo"]


async def test_copy_and_paste_use_the_remote_clipboard_commands():
    s = _streamer()
    await s.dispatch({"t": "key", "key": "c", "ctrl": True})
    await s.dispatch({"t": "key", "key": "v", "ctrl": True})
    assert s._cdp.calls[0][1]["commands"] == ["copy"]
    assert s._cdp.calls[2][1]["commands"] == ["paste"]
