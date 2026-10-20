"""Capture a reusable session by logging in once in a real browser window.

    python examples/capture_session.py chatgpt sessions/chatgpt.json

A Chromium window opens; log in (and clear any CAPTCHA). As soon as the library
detects you're authenticated, it saves a storageState JSON and closes.
"""

import asyncio
import sys

from chatsherpa.providers import get_provider


async def main(provider_name: str, out_path: str) -> None:
    provider_cls = get_provider(provider_name)
    path = await provider_cls.capture_session(out_path, timeout_s=300)
    print(f"\nSession saved to {path}. Reuse it headlessly with send_message.py")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python examples/capture_session.py <provider> <out.json>")
        raise SystemExit(2)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
