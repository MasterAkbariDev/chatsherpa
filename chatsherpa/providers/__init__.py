"""Provider adapters and a name-based registry."""

from __future__ import annotations

from typing import Dict, Type

from ..base import BaseProvider
from .chatgpt import ChatGPTProvider
from .deepseek import DeepSeekProvider
from .gemini import GeminiProvider
from .grok import GrokProvider

REGISTRY: Dict[str, Type[BaseProvider]] = {
    ChatGPTProvider.name: ChatGPTProvider,
    DeepSeekProvider.name: DeepSeekProvider,
    GrokProvider.name: GrokProvider,
    GeminiProvider.name: GeminiProvider,
}


def get_provider(name: str) -> Type[BaseProvider]:
    """Look up a provider class by its ``name`` (case-insensitive)."""
    try:
        return REGISTRY[name.lower()]
    except KeyError:
        raise KeyError(
            f"Unknown provider {name!r}. Available: {', '.join(sorted(REGISTRY))}"
        )


__all__ = [
    "REGISTRY",
    "get_provider",
    "ChatGPTProvider",
    "DeepSeekProvider",
    "GrokProvider",
    "GeminiProvider",
]
