"""Ask the same prompt to several providers at once and compare answers.

    python examples/parallel_ask.py "What is a vector database?"

Expects session files at sessions/<provider>.json for each provider you list.
"""

import asyncio
import sys

from chatsherpa import Session
from chatsherpa.providers import get_provider

PROVIDERS = ["chatgpt", "deepseek", "grok", "gemini"]


async def ask_one(name: str, prompt: str) -> tuple[str, str]:
    provider_cls = get_provider(name)
    try:
        session = Session.from_storage_state(f"sessions/{name}.json")
        async with provider_cls(session) as bot:
            return name, await bot.send(prompt)
    except Exception as exc:  # keep one failure from sinking the others
        return name, f"<error: {exc}>"


async def main(prompt: str) -> None:
    results = await asyncio.gather(*(ask_one(n, prompt) for n in PROVIDERS))
    for name, answer in results:
        print(f"\n===== {name} =====\n{answer}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print('usage: python examples/parallel_ask.py "your prompt"')
        raise SystemExit(2)
    asyncio.run(main(sys.argv[1]))
