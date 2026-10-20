"""Session / cookie handling.

A :class:`Session` is a flexible wrapper over the two ways you can hand
authentication to a provider:

* **storage state** — a Playwright ``storageState`` JSON (cookies + localStorage),
  the most robust option. Capture it once with ``Provider.capture_session(...)``.
* **raw cookies** — a list of cookie dicts, or a raw ``"k=v; k2=v2"`` header
  string (e.g. exported from a browser extension) plus the target domain.

Both can be combined: storage state is applied when the context is created and
raw cookies are injected on top afterwards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

from .exceptions import SessionError

SessionSource = Union["Session", str, Path, dict, list, None]


def _normalize_cookie(raw: Dict[str, Any], default_domain: Optional[str]) -> Dict[str, Any]:
    """Coerce a loosely-shaped cookie dict into Playwright's expected form."""
    name = raw.get("name") or raw.get("Name")
    value = raw.get("value") or raw.get("Value")
    if name is None or value is None:
        raise SessionError(f"Cookie is missing name/value: {raw!r}")

    cookie: Dict[str, Any] = {"name": str(name), "value": str(value)}

    domain = raw.get("domain") or raw.get("Domain") or default_domain
    path = raw.get("path") or raw.get("Path") or "/"
    if domain:
        cookie["domain"] = domain.lstrip(".") if raw.get("url") is None else domain
        cookie["path"] = path
    if raw.get("url"):
        cookie["url"] = raw["url"]
    # Optional fields Playwright understands.
    for src, dst in (
        ("expires", "expires"),
        ("expirationDate", "expires"),
        ("httpOnly", "httpOnly"),
        ("secure", "secure"),
        ("sameSite", "sameSite"),
    ):
        if src in raw and raw[src] is not None:
            cookie[dst] = raw[src]
    # Playwright wants sameSite in {"Strict","Lax","None"}. Browser-extension
    # exports use varied spellings ("no_restriction", "unspecified", ...).
    if "sameSite" in cookie:
        raw_ss = str(cookie["sameSite"]).strip().lower()
        cookie["sameSite"] = {
            "no_restriction": "None",
            "none": "None",
            "unspecified": "Lax",
            "lax": "Lax",
            "strict": "Strict",
        }.get(raw_ss, "Lax")
    if "expires" in cookie:
        try:
            cookie["expires"] = float(cookie["expires"])
        except (TypeError, ValueError):
            cookie.pop("expires")
    return cookie


def _parse_cookie_header(header: str, domain: str) -> List[Dict[str, Any]]:
    """Parse a ``"a=b; c=d"`` cookie header into cookie dicts for ``domain``."""
    cookies: List[Dict[str, Any]] = []
    for part in header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies.append(
            {"name": name.strip(), "value": value.strip(), "domain": domain, "path": "/"}
        )
    return cookies


@dataclass
class Session:
    """Authentication material for a provider context."""

    storage_state_path: Optional[str] = None
    storage_state: Optional[dict] = None
    cookies: List[Dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Constructors
    # ------------------------------------------------------------------ #
    @classmethod
    def from_storage_state(cls, path_or_dict: Union[str, Path, dict]) -> "Session":
        if isinstance(path_or_dict, dict):
            return cls(storage_state=path_or_dict)
        return cls(storage_state_path=str(path_or_dict))

    @classmethod
    def from_cookies(cls, cookies: List[dict], default_domain: Optional[str] = None) -> "Session":
        return cls(cookies=[_normalize_cookie(c, default_domain) for c in cookies])

    @classmethod
    def from_cookie_string(cls, header: str, domain: str) -> "Session":
        """Build a session from a raw ``Cookie:`` header string + a domain.

        ``domain`` may be a bare host (``"chatgpt.com"``) or a full URL.
        """
        if "://" in domain:
            domain = urlparse(domain).hostname or domain
        return cls(cookies=_parse_cookie_header(header, domain))

    @classmethod
    def from_json_file(cls, path: Union[str, Path]) -> "Session":
        """Load a JSON file that is either a storageState or a cookie array."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and "cookies" in data and "origins" in data:
            return cls(storage_state=data)
        if isinstance(data, list):
            return cls.from_cookies(data)
        if isinstance(data, dict) and "cookies" in data:
            return cls.from_cookies(data["cookies"])
        raise SessionError(f"Unrecognized session JSON shape in {path}")

    @classmethod
    def coerce(cls, value: SessionSource) -> "Session":
        """Turn whatever the caller passed into a :class:`Session`."""
        if value is None:
            return cls()
        if isinstance(value, Session):
            return value
        if isinstance(value, (str, Path)):
            p = Path(value)
            if p.exists():
                return cls.from_json_file(p)
            raise SessionError(
                f"Session path does not exist: {value!r}. "
                "Pass a storageState file, a Session, cookies list, or use "
                "Session.from_cookie_string(header, domain)."
            )
        if isinstance(value, dict):
            if "cookies" in value and "origins" in value:
                return cls(storage_state=value)
            if "cookies" in value:
                return cls.from_cookies(value["cookies"])
            raise SessionError("Dict session must look like a Playwright storageState.")
        if isinstance(value, list):
            return cls.from_cookies(value)
        raise SessionError(f"Cannot coerce session from {type(value).__name__}")

    # ------------------------------------------------------------------ #
    # Consumption
    # ------------------------------------------------------------------ #
    def storage_state_arg(self) -> Optional[Union[str, dict]]:
        """Return a value suitable for ``browser.new_context(storage_state=...)``."""
        if self.storage_state_path and Path(self.storage_state_path).exists():
            return self.storage_state_path
        if self.storage_state:
            return self.storage_state
        return None

    def cookies_for(self, url: str) -> List[Dict[str, Any]]:
        """Cookies to inject via ``context.add_cookies`` after context creation.

        Any cookie missing both ``url`` and ``domain`` is given the page's host.
        """
        host = urlparse(url).hostname
        out: List[Dict[str, Any]] = []
        for c in self.cookies:
            c = dict(c)
            if "url" not in c and "domain" not in c and host:
                c["domain"] = host
                c.setdefault("path", "/")
            out.append(c)
        return out

    def is_empty(self) -> bool:
        return self.storage_state_arg() is None and not self.cookies

    def to_storage_state(self) -> Dict[str, Any]:
        """Return a Playwright ``storageState`` dict for this session.

        Lets a cookie-only session (e.g. a browser-extension export) be stored
        and reused the same way a captured storageState is.
        """
        if self.storage_state:
            return self.storage_state
        cookies: List[Dict[str, Any]] = []
        for c in self.cookies:
            domain = c.get("domain", "")
            if not domain and c.get("url"):
                domain = urlparse(c["url"]).hostname or ""
            if not domain:
                continue  # storageState cookies require a domain
            cookies.append({
                "name": c["name"],
                "value": c["value"],
                "domain": domain,
                "path": c.get("path", "/"),
                "expires": float(c.get("expires", -1)),
                "httpOnly": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", True)),
                "sameSite": c.get("sameSite", "Lax"),
            })
        return {"cookies": cookies, "origins": []}
