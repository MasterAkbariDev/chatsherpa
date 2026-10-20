"""Fresh standalone bot with aiogram v3.

Setup:
    pip install -e '.[telegram]' aiogram
    chatsherpa serve            # login server (separate terminal)
    BOT_TOKEN=... PUBLIC_BASE_URL=https://login.you.dev python examples/telegram_fresh_aiogram.py
"""

import asyncio
import os

from aiogram import Bot, Dispatcher

from chatsherpa import ChatSherpa, FileSessionStore
from chatsherpa.integrations import TelegramBridge


async def main() -> None:
    bridge = ChatSherpa(store=FileSessionStore("sessions", encryption_key=os.getenv("ENCRYPTION_KEY")))
    dp = Dispatcher()
    TelegramBridge(
        bridge,
        command_prefix="ai",
        login_server_url=os.environ["PUBLIC_BASE_URL"],
        api_secret=os.getenv("API_SECRET"),
        connect_providers=("gemini",),
    ).register_aiogram(dp)

    await dp.start_polling(Bot(os.environ["BOT_TOKEN"]))


if __name__ == "__main__":
    asyncio.run(main())
