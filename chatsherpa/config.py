"""Minimal .env loader and per-provider credential/session resolver.

No third-party dependency: parses simple ``KEY=VALUE`` lines. Values already
present in the real environment take precedence over the file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


def app_home() -> Path:
    """Directory that holds ``.env`` and ``sessions``, independent of cwd.

    ``CHATSHERPA_HOME`` wins. Otherwise an editable install (this repo, or
    ``/opt/chatsherpa``) is used, then a parent of the working directory that
    already contains ``.env`` or ``sessions``.
    """
    override = os.environ.get("CHATSHERPA_HOME")
    if override:
        return Path(override).expanduser().resolve()
    root = Path(__file__).resolve().parents[1]
    if (root / "pyproject.toml").exists() and (root / "chatsherpa").is_dir():
        return root
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".env").exists() or (candidate / "sessions").is_dir():
            return candidate
    return cwd


def load_env(path: str = ".env") -> Dict[str, str]:
    """Parse a .env file into a dict (does not mutate os.environ)."""
    data: Dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return data
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


@dataclass
class ProviderCredentials:
    provider: str
    session_path: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None

    @property
    def has_session(self) -> bool:
        return bool(self.session_path and Path(self.session_path).exists())


def resolve(provider: str, env_path: str = ".env") -> ProviderCredentials:
    """Resolve credentials/session for ``provider`` from env + .env file."""
    file_env = load_env(env_path)

    def get(suffix: str) -> Optional[str]:
        key = f"{provider.upper()}_{suffix}"
        val = os.environ.get(key) or file_env.get(key)
        return val or None

    return ProviderCredentials(
        provider=provider.lower(),
        session_path=get("SESSION"),
        email=get("EMAIL"),
        password=get("PASSWORD"),
    )
