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
        # X servers also listen on an abstract socket that a private /tmp does not
        # isolate, so a number can be held by a server this process cannot see.
        # Try the next number whenever one fails to come up.
        for number in range(99, 120):
            sock = Path(f"/tmp/.X11-unix/X{number}")
            if sock.exists():
                continue
            name = f":{number}"
            proc = subprocess.Popen(
                ["Xvfb", name, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(100):  # up to ~5s for the display to come up
                if proc.poll() is not None:
                    break
                if sock.exists():
                    self._proc = proc
                    os.environ["DISPLAY"] = name
                    return True
                time.sleep(0.05)
            if proc.poll() is None:
                proc.kill()
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


