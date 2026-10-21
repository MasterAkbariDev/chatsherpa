"""Telegram integration — a framework-agnostic core plus thin bot adapters.

The library never owns a ``Bot``. A host mounts our handlers onto its *existing*
``python-telegram-bot`` ``Application`` (or aiogram ``Dispatcher``/``Router``)::

    from chatsherpa import ChatSherpa
    from chatsherpa.integrations import TelegramBridge

    bridge = ChatSherpa(store=FileSessionStore("sessions"))
    TelegramBridge(bridge, login_server_url="https://login.example.com",
                   api_secret="...").register(application)

Everything is namespaced under ``command_prefix`` so it coexists with the host's
own handlers. The core :meth:`TelegramBridge.handle` is also a usable escape
hatch for any framework: give it a user id and the raw message text, get back a
reply string.

Importing this module never hard-requires ``python-telegram-bot``; the HTTP call
to the login server uses the stdlib (``urllib``) in a thread, like
:class:`chatsherpa.events.WebhookSink`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from typing import Dict, Optional, Union

from ..client import ChatSherpa
from ..exceptions import ChatFullError, LoginRequiredError
from ..store import DEFAULT_CHAT

log = logging.getLogger("chatsherpa.integrations.telegram")

UserId = Union[str, int]


class TelegramBridge:
    """Parse Telegram-style commands and route them to an :class:`ChatSherpa`.

    Framework-agnostic: :meth:`handle` does all the work and returns a reply
    string. The :meth:`register` / :meth:`register_aiogram` adapters only wire a
    host's bot to it.
    """

    def __init__(
        self,
        bridge: ChatSherpa,
        command_prefix: str = "ai",
        login_mode: str = "remote",
        login_server_url: Optional[str] = None,
        api_secret: Optional[str] = None,
        default_provider: str = "chatgpt",
        link_mode: str = "live",
        connect_providers=("gemini",),
    ) -> None:
        self.bridge = bridge
        self.prefix = command_prefix
        self.login_mode = login_mode
        self.login_server_url = login_server_url.rstrip("/") if login_server_url else None
        self.api_secret = api_secret
        self.default_provider = default_provider
        # "live" = server-side live-view browser (password/2FA). "connect" = the
        # user logs in with their own browser (needed for Google / OAuth).
        self.link_mode = link_mode
        self.connect_providers = {p.lower() for p in (connect_providers or ())}
        self._user_provider: Dict[str, str] = {}  # per-user default, in-memory
        self._user_chat: Dict[str, str] = {}      # per-user named chat, in-memory

    def _mode_for(self, provider: str) -> str:
        return "connect" if provider.lower() in self.connect_providers else self.link_mode

    # ---------------- command names ----------------
    @property
    def cmd_login(self) -> str:
        return f"{self.prefix}_login"

    @property
    def cmd_use(self) -> str:
        return f"{self.prefix}_use"

    @property
    def cmd_status(self) -> str:
        return f"{self.prefix}_status"

    @property
    def cmd_chat(self) -> str:
        return f"{self.prefix}_chat"

    def provider_for(self, user_id: UserId) -> str:
        """The provider this user currently targets (their /use choice or default)."""
        return self._user_provider.get(str(user_id), self.default_provider)

    def chat_for(self, user_id: UserId) -> str:
        """The named chat this user's messages continue (their /chat choice or default)."""
        return self._user_chat.get(str(user_id), DEFAULT_CHAT)

    # ---------------- core ----------------
    async def handle(self, user_id: UserId, text: str) -> str:
        """Parse one message and return the reply string."""
        uid = str(user_id)
        text = (text or "").strip()
        if not text:
            return "Send me a message, or try /%s_status." % self.prefix

        if text.startswith("/"):
            name, _, rest = text[1:].partition(" ")
            name = name.split("@", 1)[0]  # strip /cmd@botname
            arg = rest.strip()
            if name == self.cmd_login:
                return await self._do_login(uid, arg)
            if name == self.cmd_use:
                return self._do_use(uid, arg)
            if name == self.cmd_status:
                return await self._do_status(uid)
            if name == self.cmd_chat:
                return await self._do_chat(uid, arg)
            if name == self.prefix:
                return await self._do_ask(uid, arg)
            # unknown /command we don't own — ignore quietly
            return ""

        # plain text → ask the user's current provider
        return await self._do_ask(uid, text)

    async def _do_ask(self, uid: str, message: str) -> str:
        message = message.strip()
        if not message:
            return "Usage: /%s <your message>" % self.prefix
        provider = self.provider_for(uid)
        try:
            return await self.bridge.ask(provider, uid, message, chat=self.chat_for(uid))
        except ChatFullError:
            return (f"This {provider} chat is full. Start a new one with "
                    f"/{self.prefix}_chat <new name>, then send your message again.")
        except LoginRequiredError:
            return (f"Your {provider} session has expired. "
                    f"Run /{self.cmd_login} {provider} to sign in again.")

    def _do_use(self, uid: str, provider: str) -> str:
        provider = provider.strip().lower()
        if not provider:
            return "Usage: /%s <provider> (e.g. chatgpt, grok, gemini, deepseek)" % self.cmd_use
        self._user_provider[uid] = provider
        return f"Default provider set to {provider}."

    async def _do_chat(self, uid: str, name: str) -> str:
        """``/ai_chat`` lists this user's saved chats; ``/ai_chat <name>`` switches to one.

        A name that has not been used yet starts a new chat on the next message.
        """
        name = name.strip().split()[0].lower() if name.strip() else ""
        provider = self.provider_for(uid)
        if not name:
            chats = await self.bridge.list_chats(provider, uid)
            current = self.chat_for(uid)
            if not chats:
                return (f"No saved {provider} chats yet. Send a message to start one. "
                        f"Current chat: {current}.")
            lines = [f"{'▸' if n == current else ' '} {n}" for n in sorted(chats)]
            return f"Your {provider} chats (▸ current):\n" + "\n".join(lines)
        self._user_chat[uid] = name
        return f"Switched to chat '{name}'. Your next messages continue it."

    async def _do_status(self, uid: str) -> str:
        from ..providers import REGISTRY
        current = self.provider_for(uid)
        lines = []
        for name in REGISTRY:
            try:
                ok = await self.bridge.has_session(name, uid)
            except Exception:  # noqa: BLE001 - never let status crash on one provider
                ok = False
            mark = "✅" if ok else "—"
            star = " (default)" if name == current else ""
            lines.append(f"{mark} {name}{star}")
        return "Your sessions:\n" + "\n".join(lines)

    async def _do_login(self, uid: str, provider: str) -> str:
        provider = provider.strip().lower() or self.provider_for(uid)
        if self.login_mode == "remote" and self.login_server_url:
            try:
                info = await self._request_login_link(provider, uid)
            except Exception as exc:  # noqa: BLE001
                log.warning("login link request failed: %s", exc)
                return "Couldn't reach the login server right now. Please try again shortly."
            url = info.get("url")
            if not url:
                return "The login server didn't return a link. Please try again."
            return f"Open this link to sign in to {provider}:\n{url}"
        return (f"To sign in to {provider}, run `chatsherpa login` on the machine "
                f"that hosts the bridge, then try again.")

    async def _request_login_link(self, provider: str, user_id: str) -> dict:
        """POST to the login server's /api/login, off the event loop."""
        body = json.dumps({"provider": provider, "user_id": user_id,
                           "mode": self._mode_for(provider)}).encode("utf-8")
        return await asyncio.get_event_loop().run_in_executor(
            None, self._post_login, body
        )

    def _post_login(self, body: bytes) -> dict:
        headers = {"Content-Type": "application/json", "User-Agent": "chatsherpa"}
        if self.api_secret:
            headers["X-ChatSherpa-Secret"] = self.api_secret
        req = urllib.request.Request(
            f"{self.login_server_url}/api/login", data=body, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ---------------- adapters ----------------
    def register(self, application) -> None:
        """Mount handlers on a python-telegram-bot v20+ ``Application``.

        Adds a ``CommandHandler`` for each ``<prefix>_*`` command plus the bare
        ``<prefix>`` command, and a ``MessageHandler`` for plain text. Everything
        is namespaced under ``command_prefix`` so it coexists with the host's own
        handlers. Imported lazily so this module never requires PTB.
        """
        try:
            from telegram.ext import (
                CommandHandler,
                MessageHandler,
                filters,
            )
        except ImportError as exc:  # pragma: no cover - exercised only without PTB
            raise RuntimeError(
                "python-telegram-bot is required for register(); "
                "install it with: pip install 'python-telegram-bot>=21'"
            ) from exc

        async def _reply(update, _context):
            msg = update.effective_message
            if msg is None:
                return
            user = update.effective_user
            uid = user.id if user else (update.effective_chat.id if update.effective_chat else "anon")
            reply = await self.handle(uid, msg.text or "")
            if reply:
                await msg.reply_text(reply)

        for command in (self.prefix, self.cmd_login, self.cmd_use, self.cmd_status, self.cmd_chat):
            application.add_handler(CommandHandler(command, _reply))
        # plain text (not a command) → ask
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _reply))

    def register_aiogram(self, dispatcher_or_router) -> None:
        """Mount handlers on an aiogram v3 ``Dispatcher`` or ``Router``.

        Same delegation pattern as :meth:`register`: command messages for our
        namespace and plain text both route through :meth:`handle`. Imported
        lazily so this module never requires aiogram.
        """
        try:
            from aiogram import F
            from aiogram.filters import Command
        except ImportError as exc:  # pragma: no cover - exercised only without aiogram
            raise RuntimeError(
                "aiogram is required for register_aiogram(); "
                "install it with: pip install 'aiogram>=3'"
            ) from exc

        async def _reply(message) -> None:
            uid = message.from_user.id if message.from_user else message.chat.id
            reply = await self.handle(uid, message.text or "")
            if reply:
                await message.answer(reply)

        commands = (self.prefix, self.cmd_login, self.cmd_use, self.cmd_status, self.cmd_chat)
        dispatcher_or_router.message.register(_reply, Command(*commands))
        # plain text that isn't one of our commands
        dispatcher_or_router.message.register(_reply, F.text & ~F.text.startswith("/"))
