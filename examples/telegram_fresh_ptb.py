"""Fresh standalone bot with python-telegram-bot.

Setup:
    pip install -e '.[telegram]'
    chatsherpa setup            # configure PUBLIC_BASE_URL etc.
    chatsherpa serve            # run the login server (separate terminal)
    BOT_TOKEN=... PUBLIC_BASE_URL=https://login.you.dev python examples/telegram_fresh_ptb.py

Commands your users get: /ai <msg>, /ai_login <provider>, /ai_use <provider>, /ai_status
"""

import os

from telegram.ext import ApplicationBuilder

from chatsherpa import ChatSherpa, FileSessionStore, WebhookSink
from chatsherpa.integrations import TelegramBridge


def main() -> None:
    bridge = ChatSherpa(store=FileSessionStore("sessions", encryption_key=os.getenv("ENCRYPTION_KEY")))
    if os.getenv("WEBHOOK_URL"):
        bridge.add_sink(WebhookSink(os.environ["WEBHOOK_URL"], secret=os.getenv("WEBHOOK_SECRET")))

    app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()
    TelegramBridge(
        bridge,
        command_prefix="ai",
        login_server_url=os.environ["PUBLIC_BASE_URL"],
        api_secret=os.getenv("API_SECRET"),
        default_provider="chatgpt",
        # Google-gated providers use "connect" mode (user logs in with their own browser):
        connect_providers=("gemini",),
    ).register(app)

    app.run_polling()


if __name__ == "__main__":
    main()
