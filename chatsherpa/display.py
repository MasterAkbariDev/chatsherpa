"""A virtual display for headed Chromium on a server without a monitor.

Cloudflare blocks Playwright's headless Chromium from a datacenter IP, so a
saved ChatGPT session looks logged out. The same browser, headed, on Xvfb,
is let through.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional


class VirtualDisplay:
    """Start Xvfb when the process has no ``DISPLAY``."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None

    def start_if_needed(self) -> bool:
        """Start Xvfb only when nothing is displaying yet.

        Returns True when this call started a display. A display that was
        already set is left alone.
        """
        if os.environ.get("DISPLAY") or not shutil.which("Xvfb"):
            return False
        if self._proc and self._proc.poll() is None:
            return True
        number = _free_display()
        name = f":{number}"
        self._proc = subprocess.Popen(
            ["Xvfb", name, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        sock = Path(f"/tmp/.X11-unix/X{number}")
        for _ in range(40):
            if sock.exists() and self._proc.poll() is None:
                os.environ["DISPLAY"] = name
                return True
            time.sleep(0.05)
        self.stop()
        return False

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def _free_display() -> int:
    for number in range(99, 200):
        if not Path(f"/tmp/.X11-unix/X{number}").exists():
            return number
    return 99
