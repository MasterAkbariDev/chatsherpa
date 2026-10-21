"""Exception hierarchy for chatsherpa."""


class BridgeError(Exception):
    """Base class for all library errors."""


class ProviderError(BridgeError):
    """Something went wrong driving a provider's web UI."""


class LoginRequiredError(ProviderError):
    """The provider page loaded but no authenticated session was detected.

    Usually means the supplied cookies / storage state are missing, expired,
    or belong to a different account. Re-capture a session with
    ``Provider.capture_session(...)`` or the ``chatsherpa login`` CLI command.
    """


class ResponseTimeoutError(ProviderError):
    """The assistant did not produce a (stable) response within the timeout."""


class RateLimitedError(ProviderError):
    """The provider is busy / rate-limited (e.g. "Grok is experiencing high
    demand", "You've reached your limit", "at capacity")."""


class ChatFullError(ProviderError):
    """The provider refuses new messages in this chat (for example ChatGPT's
    "Chat memory full"). Start a new chat instead of retrying this one."""


class SessionError(BridgeError):
    """A session source could not be parsed or loaded."""
