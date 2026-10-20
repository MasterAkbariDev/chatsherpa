"""Local (headful) login — opens a browser on the user's own machine."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Type, Union

from ..base import BaseProvider, ProviderConfig


async def local_login(
    provider_cls: Type[BaseProvider],
    store,
    user_id: Union[str, int] = "local",
    email: Optional[str] = None,
    password: Optional[str] = None,
    headless: bool = False,
    timeout_s: int = 300,
    config: Optional[ProviderConfig] = None,
) -> Dict[str, Any]:
    """Run the headful session catcher, then persist to ``store``.

    Returns the captured storageState dict.
    """
    tmp = Path(tempfile.mkdtemp()) / f"{provider_cls.name}.json"
    await provider_cls.capture_session(
        str(tmp), email=email, password=password, headless=headless,
        timeout_s=timeout_s, config=config,
    )
    state = json.loads(tmp.read_text("utf-8"))
    try:
        tmp.unlink()
    except OSError:
        pass
    await store.put(user_id, provider_cls.name, state)
    return state
