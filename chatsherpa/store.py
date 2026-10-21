"""Session storage, keyed by (user_id, provider).

Backends:
* :class:`MemorySessionStore` — ephemeral, for tests / single runs.
* :class:`FileSessionStore` — JSON files on disk, optionally encrypted at rest.

Encryption is opt-in and needs the ``[crypto]`` extra (``cryptography``). These
files are users' AI-account cookies on a server, so encrypting them is the
recommended default in production.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .session import Session

try:  # optional
    from cryptography.fernet import Fernet
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore

DEFAULT_USER = "local"
DEFAULT_CHAT = "default"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _slug(value: Union[str, int]) -> str:
    return _SAFE.sub("_", str(value))


class SessionStore(ABC):
    @abstractmethod
    async def get(self, user_id: Union[str, int], provider: str) -> Optional[Session]: ...

    @abstractmethod
    async def put(self, user_id: Union[str, int], provider: str, storage_state: Dict[str, Any]) -> None: ...

    @abstractmethod
    async def delete(self, user_id: Union[str, int], provider: str) -> None: ...

    @abstractmethod
    async def list(self) -> List[Tuple[str, str]]: ...

    async def get_thread(self, user_id: Union[str, int], provider: str,
                         chat: str = DEFAULT_CHAT) -> Optional[str]:
        """Conversation URL saved under ``chat`` for this user and provider, if any."""
        return None

    async def put_thread(self, user_id: Union[str, int], provider: str, url: str,
                         chat: str = DEFAULT_CHAT) -> None:
        """Remember ``url`` under ``chat`` so the next ask on that chat continues it."""
        return None

    async def list_threads(self, user_id: Union[str, int], provider: str) -> Dict[str, str]:
        """All saved chats for this user and provider, as ``{chat name: url}``."""
        return {}


class MemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self._data: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._threads: Dict[Tuple[str, str], Dict[str, str]] = {}

    async def get(self, user_id, provider):
        state = self._data.get((_slug(user_id), provider.lower()))
        return Session.from_storage_state(state) if state else None

    async def put(self, user_id, provider, storage_state):
        self._data[(_slug(user_id), provider.lower())] = storage_state

    async def delete(self, user_id, provider):
        self._data.pop((_slug(user_id), provider.lower()), None)

    async def list(self):
        return [(u, p) for (u, p) in self._data]

    async def get_thread(self, user_id, provider, chat=DEFAULT_CHAT):
        return self._threads.get((_slug(user_id), provider.lower()), {}).get(chat)

    async def put_thread(self, user_id, provider, url, chat=DEFAULT_CHAT):
        self._threads.setdefault((_slug(user_id), provider.lower()), {})[chat] = url.strip()

    async def list_threads(self, user_id, provider):
        return dict(self._threads.get((_slug(user_id), provider.lower()), {}))


class FileSessionStore(SessionStore):
    """One JSON file per (user, provider) under ``directory``.

    Layout stays friendly to the CLI/scripts: the default user writes
    ``<dir>/<provider>.json``; other users get ``<dir>/<provider>.<user>.json``.
    With ``encryption_key`` set, files are written as ``.json.enc`` ciphertext.
    """

    def __init__(self, directory: Union[str, Path] = "sessions", encryption_key: Optional[str] = None):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fernet = None
        if encryption_key:
            if Fernet is None:
                raise RuntimeError(
                    "encryption_key set but 'cryptography' is not installed. "
                    "Install with: pip install 'chatsherpa[crypto]'"
                )
            self._fernet = Fernet(encryption_key)

    def path_for(self, user_id: Union[str, int], provider: str) -> Path:
        provider = provider.lower()
        name = provider if _slug(user_id) == DEFAULT_USER else f"{provider}.{_slug(user_id)}"
        ext = ".json.enc" if self._fernet else ".json"
        return self.dir / f"{name}{ext}"

    # plaintext fallback path (so we can read a session saved before encryption)
    def _plain_path(self, user_id, provider) -> Path:
        provider = provider.lower()
        name = provider if _slug(user_id) == DEFAULT_USER else f"{provider}.{_slug(user_id)}"
        return self.dir / f"{name}.json"

    async def get(self, user_id, provider):
        p = self.path_for(user_id, provider)
        if self._fernet and p.exists():
            raw = self._fernet.decrypt(p.read_bytes())
            return Session.from_storage_state(json.loads(raw))
        plain = self._plain_path(user_id, provider)
        if plain.exists():
            return Session.from_storage_state(json.loads(plain.read_text("utf-8")))
        return None

    async def put(self, user_id, provider, storage_state):
        p = self.path_for(user_id, provider)
        data = json.dumps(storage_state).encode("utf-8")
        if self._fernet:
            p.write_bytes(self._fernet.encrypt(data))
        else:
            p.write_bytes(data)

    def _thread_path(self, user_id, provider) -> Path:
        provider = provider.lower()
        name = provider if _slug(user_id) == DEFAULT_USER else f"{provider}.{_slug(user_id)}"
        return self.dir / f"{name}.thread"

    # Named chats live in one ``.chats`` JSON map per (user, provider). The
    # extension keeps it out of the ``*.json*`` session glob in list().
    def _chats_path(self, user_id, provider) -> Path:
        provider = provider.lower()
        name = provider if _slug(user_id) == DEFAULT_USER else f"{provider}.{_slug(user_id)}"
        return self.dir / f"{name}.chats"

    def _read_chats(self, user_id, provider) -> Dict[str, str]:
        p = self._chats_path(user_id, provider)
        if p.exists():
            return json.loads(p.read_text("utf-8") or "{}")
        legacy = self._thread_path(user_id, provider)  # single-chat files from before named chats
        if legacy.exists():
            url = legacy.read_text("utf-8").strip()
            return {DEFAULT_CHAT: url} if url else {}
        return {}

    async def get_thread(self, user_id, provider, chat=DEFAULT_CHAT):
        return self._read_chats(user_id, provider).get(chat) or None

    async def put_thread(self, user_id, provider, url, chat=DEFAULT_CHAT):
        chats = self._read_chats(user_id, provider)
        chats[chat] = url.strip()
        self._chats_path(user_id, provider).write_text(
            json.dumps(chats, indent=2) + "\n", encoding="utf-8")

    async def list_threads(self, user_id, provider):
        return dict(self._read_chats(user_id, provider))

    async def delete(self, user_id, provider):
        for p in (self.path_for(user_id, provider), self._plain_path(user_id, provider),
                  self._thread_path(user_id, provider), self._chats_path(user_id, provider)):
            if p.exists():
                p.unlink()

    async def list(self):
        out: List[Tuple[str, str]] = []
        for f in self.dir.glob("*.json*"):
            stem = f.name.split(".json")[0]
            provider, _, user = stem.partition(".")
            out.append((user or DEFAULT_USER, provider))
        return out
