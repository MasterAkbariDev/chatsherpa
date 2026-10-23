"""Live-view streaming: screencast a server-side page to the browser and relay
input back, following OAuth popups (so "Continue with Google" works).

This drives the Chrome DevTools Protocol directly through Playwright's
``new_cdp_session``. It needs a live browser to exercise end to end; the unit
tests cover the pure input-coordinate mapping only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

log = logging.getLogger("chatsherpa.login.remote")

# viewer key events -> CDP key payloads for non-character keys
_SPECIAL_KEYS = {
    "Enter": ("Enter", 13), "Backspace": ("Backspace", 8), "Tab": ("Tab", 9),
    "Escape": ("Escape", 27), "ArrowLeft": ("ArrowLeft", 37), "ArrowUp": ("ArrowUp", 38),
    "ArrowRight": ("ArrowRight", 39), "ArrowDown": ("ArrowDown", 40), "Delete": ("Delete", 46),
    "Home": ("Home", 36), "End": ("End", 35),
}
# The editing command a key means in a text field. Chromium needs it with the key, or the key does nothing.
_EDIT_COMMANDS = {
    "Backspace": ["deleteBackward"], "Delete": ["deleteForward"], "Enter": ["insertNewline"],
    "ArrowLeft": ["moveLeft"], "ArrowRight": ["moveRight"], "ArrowUp": ["moveUp"], "ArrowDown": ["moveDown"],
    "Home": ["moveToBeginningOfLine"], "End": ["moveToEndOfLine"],
}
# Shift extends the selection with the same movement.
_SELECT_COMMANDS = {
    "ArrowLeft": "moveLeftAndModifySelection", "ArrowRight": "moveRightAndModifySelection",
    "ArrowUp": "moveUpAndModifySelection", "ArrowDown": "moveDownAndModifySelection",
    "Home": "moveToBeginningOfLineAndModifySelection", "End": "moveToEndOfLineAndModifySelection",
}
# Ctrl/Cmd shortcuts for text: the letter -> the command.
_SHORTCUT_COMMANDS = {"a": "selectAll", "c": "copy", "v": "paste", "x": "cut", "z": "undo", "y": "redo"}
# Chromium modifier bits: Alt 1, Ctrl 2, Meta 4, Shift 8.
def _modifiers(msg: Dict[str, Any]) -> int:
    return (1 if msg.get("alt") else 0) | (2 if msg.get("ctrl") else 0) | (4 if msg.get("meta") else 0) | (8 if msg.get("shift") else 0)


class LiveStreamer:
    """Streams one page at a time, switching to popups as they appear."""

    def __init__(self, send_json: Callable[[Dict[str, Any]], Awaitable[None]], context: Any, main_page: Any):
        self._send = send_json
        self._context = context
        self._main_page = main_page
        self._page = main_page
        self._cdp = None
        self._meta = {"w": 1280, "h": 720}
        self._stopped = False
        self._tasks: set = set()

    async def start(self) -> None:
        self._context.on("page", self._on_new_page)
        await self._attach(self._main_page)

    async def _attach(self, page: Any) -> None:
        if self._stopped:
            return
        self._page = page
        self._cdp = await self._context.new_cdp_session(page)
        self._cdp.on("Page.screencastFrame", self._frame_handler)
        await self._cdp.send("Page.startScreencast",
                             {"format": "jpeg", "quality": 60, "everyNthFrame": 1})

    async def _detach(self) -> None:
        if self._cdp:
            try:
                await self._cdp.send("Page.stopScreencast")
                await self._cdp.detach()
            except Exception:
                pass
            self._cdp = None

    def _frame_handler(self, params: Dict[str, Any]) -> None:
        t = asyncio.create_task(self._on_frame(params))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _on_frame(self, params: Dict[str, Any]) -> None:
        md = params.get("metadata", {})
        if md.get("deviceWidth"):
            self._meta = {"w": md["deviceWidth"], "h": md["deviceHeight"]}
        try:
            await self._send({"type": "frame", "data": params["data"]})
            if self._cdp:
                await self._cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        except Exception:
            pass

    def _on_new_page(self, page: Any) -> None:
        async def swap():
            await self._detach()
            await self._attach(page)
            page.on("close", lambda: asyncio.create_task(self._back_to_main()))
        t = asyncio.create_task(swap())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _back_to_main(self) -> None:
        if self._stopped:
            return
        await self._detach()
        await self._attach(self._main_page)

    async def stop(self) -> None:
        self._stopped = True
        await self._detach()

    # -------- input relay --------
    def _to_px(self, nx: float, ny: float):
        return nx * self._meta["w"], ny * self._meta["h"]

    async def _key(self, msg: Dict[str, Any]) -> None:
        """A key press. Edit keys and Ctrl/Cmd shortcuts carry the editing command Chromium acts on."""
        key = msg.get("key", "")
        mods = _modifiers(msg)
        if key in _SPECIAL_KEYS:
            kname, vk = _SPECIAL_KEYS[key]
            commands = list(_EDIT_COMMANDS.get(key, []))
            if msg.get("shift") and key in _SELECT_COMMANDS:
                commands = [_SELECT_COMMANDS[key]]
            payload = {"key": kname, "code": kname, "windowsVirtualKeyCode": vk, "modifiers": mods}
        elif (msg.get("ctrl") or msg.get("meta")) and len(key) == 1:
            letter = key.lower()
            commands = [_SHORTCUT_COMMANDS[letter]] if letter in _SHORTCUT_COMMANDS else []
            vk = ord(letter.upper())
            payload = {"key": letter, "code": f"Key{letter.upper()}", "windowsVirtualKeyCode": vk, "modifiers": mods}
        else:
            return
        # rawKeyDown is the key itself; the command (when there is one) is what the page acts on.
        down = {"type": "rawKeyDown", **payload}
        if commands:
            down["commands"] = commands
        await self._cdp.send("Input.dispatchKeyEvent", down)
        await self._cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", **payload})

    async def _nav(self, action: str) -> None:
        """Back, forward and reload, in the page the person is looking at."""
        page = self._page
        if action == "back":
            await page.go_back(wait_until="domcontentloaded")
        elif action == "forward":
            await page.go_forward(wait_until="domcontentloaded")
        elif action == "reload":
            await page.reload(wait_until="domcontentloaded")

    async def dispatch(self, msg: Dict[str, Any]) -> None:
        if not self._cdp or self._stopped:
            return
        t = msg.get("t")
        try:
            if t in ("move", "down", "up"):
                x, y = self._to_px(msg["x"], msg["y"])
                etype = {"move": "mouseMoved", "down": "mousePressed", "up": "mouseReleased"}[t]
                payload = {"type": etype, "x": x, "y": y}
                if t != "move":
                    payload.update({"button": msg.get("button", "left"), "clickCount": 1})
                await self._cdp.send("Input.dispatchMouseEvent", payload)
            elif t == "wheel":
                x, y = self._to_px(msg["x"], msg["y"])
                await self._cdp.send("Input.dispatchMouseEvent", {
                    "type": "mouseWheel", "x": x, "y": y,
                    "deltaX": msg.get("dx", 0), "deltaY": msg.get("dy", 0)})
            elif t == "char":
                await self._cdp.send("Input.insertText", {"text": msg["text"]})
            elif t == "key":
                await self._key(msg)
            elif t == "nav":
                await self._nav(msg.get("action", ""))
        except Exception as exc:
            log.debug("input dispatch failed: %s", exc)
