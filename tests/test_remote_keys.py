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
    s._tasks = set()
    s._pending_move = None
    s._move_task = None
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


async def test_mouse_moves_are_coalesced_so_a_click_is_not_queued_behind_them():
    import asyncio
    s = _streamer()
    for i in range(50):
        await s.dispatch({"t": "move", "x": 0.1 + i * 0.001, "y": 0.5})
    await s.dispatch({"t": "down", "x": 0.5, "y": 0.5, "button": "left"})
    await asyncio.sleep(0.05)
    methods = [m for m, _ in s._cdp.calls]
    moves = [p for m, p in s._cdp.calls if m == "Input.dispatchMouseEvent" and p["type"] == "mouseMoved"]
    presses = [p for m, p in s._cdp.calls if m == "Input.dispatchMouseEvent" and p["type"] == "mousePressed"]
    assert len(moves) < 50          # the backlog collapses to the newest position
    assert presses and methods.index("Input.dispatchMouseEvent") >= 0
