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

    async def get_thread(self, user_id: Union[str, int], provider: str) -> Optional[str]:
        """Last conversation URL for this user and provider, if one was saved."""
        return None

    async def put_thread(self, user_id: Union[str, int], provider: str, url: str) -> None:
        """Remember ``url`` so the next ask continues that chat."""
        return None


class MemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self._data: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._threads: Dict[Tuple[str, str], str] = {}

    async def get(self, user_id, provider):
        state = self._data.get((_slug(user_id), provider.lower()))
        return Session.from_storage_state(state) if state else None

    async def put(self, user_id, provider, storage_state):
        self._data[(_slug(user_id), provider.lower())] = storage_state

    async def delete(self, user_id, provider):
        self._data.pop((_slug(user_id), provider.lower()), None)

    async def list(self):
        return [(u, p) for (u, p) in self._data]

    async def get_thread(self, user_id, provider):
        return self._threads.get((_slug(user_id), provider.lower()))

    async def put_thread(self, user_id, provider, url):
        self._threads[(_slug(user_id), provider.lower())] = url


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

    async def get_thread(self, user_id, provider):
        p = self._thread_path(user_id, provider)
        if not p.exists():
            return None
        url = p.read_text("utf-8").strip()
        return url or None

    async def put_thread(self, user_id, provider, url):
        self._thread_path(user_id, provider).write_text(url.strip() + "\n", encoding="utf-8")

    async def delete(self, user_id, provider):
        for p in (self.path_for(user_id, provider), self._plain_path(user_id, provider),
                  self._thread_path(user_id, provider)):
            if p.exists():
                p.unlink()

    async def list(self):
        out: List[Tuple[str, str]] = []
        for f in self.dir.glob("*.json*"):
            stem = f.name.split(".json")[0]
            provider, _, user = stem.partition(".")
            out.append((user or DEFAULT_USER, provider))
        return out
