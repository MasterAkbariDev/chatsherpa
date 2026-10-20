"""Send one message headlessly using a previously captured session.

    python examples/send_message.py chatgpt sessions/chatgpt.json "Hello there"
"""

import asyncio
import sys

from chatsherpa import Session
from chatsherpa.providers import get_provider


async def main(provider_name: str, session_path: str, prompt: str) -> None:
    provider_cls = get_provider(provider_name)
    session = Session.from_storage_state(session_path)
    async with provider_cls(session) as bot:
        response = await bot.send(prompt)
    print("\n=== response ===\n")
    print(response)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("usage: python examples/send_message.py <provider> <session.json> <prompt>")
        raise SystemExit(2)
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3]))
