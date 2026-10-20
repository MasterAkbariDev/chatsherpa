"""Add ChatSherpa to a bot you ALREADY run.

The library never owns the Bot — you keep your Application and your handlers,
and mount ours in two lines. Everything is namespaced under command_prefix
("ai"), so /ai, /ai_login, /ai_use, /ai_status won't collide with your commands.
"""

import os

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from chatsherpa import ChatSherpa, FileSessionStore
from chatsherpa.integrations import TelegramBridge


# ---- your existing bot ----
async def start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Hi, this is my existing bot.")


def main() -> None:
    app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", start))        # your own handler
    # ... your other handlers ...

    # ---- add ChatSherpa (2 lines) ----
    bridge = ChatSherpa(store=FileSessionStore("sessions"))
    TelegramBridge(
        bridge,
        command_prefix="ai",
        login_server_url=os.environ["PUBLIC_BASE_URL"],
        api_secret=os.getenv("API_SECRET"),
    ).register(app)
    # Prefer not to let plain text route to AI? Instead of register(), call
    # tg.handle(user_id, text) yourself from one of your own handlers.

    app.run_polling()


if __name__ == "__main__":
    main()
