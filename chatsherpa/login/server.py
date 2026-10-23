"""Live-view login server (the VPS component).

Hosts an isolated Chromium per login and serves a one-time link whose page is
that browser, streamed live. The user logs in (password, Google/OAuth, 2FA —
all work, it's a real browser); the catcher saves the session and shows a
success page.

Run it with ``chatsherpa serve``. Needs the ``[server]`` extra (aiohttp).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from aiohttp import WSMsgType, web
from playwright.async_api import async_playwright

from ..base import ProviderConfig
from ..client import ChatSherpa
from ..events import ErrorType, Event, EventBus, EventType
from ..exceptions import (
    BotChallengeError,
    BrowserUnavailableError,
    ChatFullError,
    LoginRequiredError,
    RateLimitedError,
    ResponseTimeoutError,
)
from ..providers import REGISTRY, get_provider
from .flow import LoginBroker
from .remote import LiveStreamer

log = logging.getLogger("chatsherpa.login.server")
_VIEWER = Path(__file__).parent / "viewer.html"
_CONNECT = Path(__file__).parent / "connect.html"


def _failure(exc: Exception) -> tuple:
    """HTTP status, error code and message for a failed ask. Shared by both ask routes."""
    if isinstance(exc, LoginRequiredError):
        return 401, "login_required", str(exc)
    if isinstance(exc, BotChallengeError):
        return 503, "bot_challenge", str(exc)
    if isinstance(exc, BrowserUnavailableError):
        return 503, "browser_unavailable", str(exc)
    if isinstance(exc, ChatFullError):
        return 409, "chat_full", str(exc)
    if isinstance(exc, RateLimitedError):
        return 429, "rate_limited", str(exc)
    if isinstance(exc, ResponseTimeoutError):
        return 504, "timeout", str(exc)
    return 500, "failed", str(exc)


class LoginServer:
    #: Seconds of silence before a streamed ask sends a status heartbeat. Keeps proxies
    #: from closing an idle connection while a provider thinks or searches.
    STREAM_HEARTBEAT_S = 5.0

    def __init__(
        self,
        store,
        public_base_url: str,
        bus: Optional[EventBus] = None,
        headless: bool = True,
        api_secret: Optional[str] = None,
        ttl: int = 600,
        host: str = "0.0.0.0",
        port: int = 8091,
        config: Optional[ProviderConfig] = None,
    ):
        self.store = store
        # Which saved sessions have actually answered an ask. A stored session that the provider refuses
        # (for example a Google session used from a server) is not "connected".
        self._verified: dict = {}
        store_dir = getattr(store, "dir", None)
        self._verified_file = Path(store_dir) / "verified.json" if store_dir else None
        if self._verified_file and self._verified_file.exists():
            try:
                self._verified = json.loads(self._verified_file.read_text("utf-8"))
            except (OSError, ValueError):
                self._verified = {}
        self.public_base_url = public_base_url.rstrip("/")
        self.bus = bus or EventBus()
        self.headless = headless
        self.api_secret = api_secret
        self.ttl = ttl
        self.host = host
        self.port = port
        self.config = config or ProviderConfig(headless=headless)
        self.broker = LoginBroker()
        self._pw = None
        self._browser = None
        # Asks are served from one long-lived bridge, so its browser pool stays
        # warm between requests instead of starting Chromium for every prompt.
        self.bridge = ChatSherpa(store=store, bus=self.bus, headless=headless, config=self.config)

    # ---------------- browser ----------------
    async def _ensure_browser(self):
        if self._browser:
            return
        self._pw = await async_playwright().start()
        # The same proxy and launch options as the provider pool. Without the proxy a sign-in
        # leaves from the server's own address, which some providers refuse (Gemini answers 403).
        # The two memory flags keep each sign-in browser small: no shared-memory use, no GPU process.
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            proxy=self.config.proxy,  # type: ignore[arg-type]
            args=[*self.config.launch_args, "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage", "--disable-gpu"],
            ignore_default_args=["--enable-automation"],
        )

    async def create_login(self, provider: str, user_id: str) -> dict:
        await self._ensure_browser()
        provider_cls = get_provider(provider)
        pending = self.broker.create(provider_cls, user_id, ttl=self.ttl)

        ctx_kwargs: dict = {}
        if self.config.user_agent:
            ctx_kwargs["user_agent"] = self.config.user_agent
        context = await self._browser.new_context(**ctx_kwargs)
        page = await context.new_page()
        await page.goto(provider_cls.home_url, wait_until="domcontentloaded",
                        timeout=self.config.nav_timeout_ms)
        pending.context = context
        pending.page = page
        pending.handle.url = f"{self.public_base_url}/login/{pending.token}"

        await self.bus.emit(Event(EventType.LOGIN_STARTED, provider=provider, user_id=user_id))
        pending.catcher = asyncio.create_task(self._catch(pending))
        return {"token": pending.token, "url": pending.handle.url, "expires_in": self.ttl}

    async def create_ingest(self, provider: str, user_id: str) -> dict:
        """A 'connect' login: no server browser. The user logs in with their own
        browser and the session arrives via /api/ingest (extension or CLI grab)."""
        provider_cls = get_provider(provider)
        pending = self.broker.create(provider_cls, user_id, ttl=self.ttl)
        pending.handle.url = f"{self.public_base_url}/connect/{pending.token}"
        await self.bus.emit(Event(EventType.LOGIN_STARTED, provider=provider_cls.name,
                                  user_id=user_id, data={"mode": "connect"}))
        return {"token": pending.token, "url": pending.handle.url, "expires_in": self.ttl}

    async def _catch(self, pending):
        provider_cls = pending.provider_cls
        prov = provider_cls.attach(pending.page, self.config)
        deadline = time.monotonic() + pending.ttl
        try:
            while time.monotonic() < deadline:
                try:
                    if await prov.is_logged_in():
                        state = await pending.context.storage_state()
                        await self.store.put(pending.user_id, provider_cls.name, state)
                        self.broker.resolve(pending.token, state)
                        await self.bus.emit(Event(EventType.LOGIN_SUCCEEDED,
                                                  provider=provider_cls.name, user_id=pending.user_id))
                        await asyncio.sleep(3)  # let the viewer render success
                        return
                except Exception:
                    pass
                await asyncio.sleep(2)
            self.broker.reject(pending.token, "timeout")
            await self.bus.emit(Event(EventType.LOGIN_TIMEOUT, provider=provider_cls.name,
                                      user_id=pending.user_id,
                                      error_type=ErrorType.LOGIN_REQUIRED,
                                      message="login not completed in time"))
        finally:
            try:
                await pending.context.close()
            except Exception:
                pass

    # ---------------- HTTP ----------------
    async def _h_health(self, request):
        return web.json_response({"ok": True, "active_logins": self.broker.active()})

    async def _h_api_login(self, request):
        if self.api_secret and request.headers.get("X-ChatSherpa-Secret") != self.api_secret:
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        provider = body.get("provider")
        user_id = str(body.get("user_id", "anon"))
        mode = body.get("mode", "live")  # "live" = server browser, "connect" = user's browser
        if not provider:
            return web.json_response({"error": "provider required"}, status=400)
        try:
            info = await (self.create_ingest(provider, user_id) if mode == "connect"
                          else self.create_login(provider, user_id))
        except KeyError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response(info)

    async def _read_ask(self, request) -> Any:
        """Checks the secret and the body shared by both ask routes.

        Returns the body (with the provider name normalised), or the error response to send.
        """
        if self.api_secret and request.headers.get("X-ChatSherpa-Secret") != self.api_secret:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)
        provider = str(body.get("provider", "")).lower()
        prompt = body.get("prompt")
        if provider not in REGISTRY or not isinstance(prompt, str) or not prompt.strip():
            return web.json_response({"error": "provider and prompt are required"}, status=400)
        body["provider"] = provider
        return body

    def _mark_verified(self, user_id: str, provider: str, ok: bool) -> None:
        key = f"{user_id}:{provider}"
        if self._verified.get(key) == ok:
            return
        self._verified[key] = ok
        if self._verified_file:
            try:
                self._verified_file.write_text(json.dumps(self._verified), "utf-8")
            except OSError as exc:
                log.warning("could not save the session status: %s", exc)

    async def _run_ask(self, body: dict, **callbacks) -> str:
        user_id = str(body.get("user_id", "local"))
        provider = body["provider"]
        try:
            text = await self.bridge.ask(
                provider, user_id, body["prompt"],
                model=body.get("model"), model_order=body.get("model_order"),
                new_chat=bool(body.get("new_chat")), account=body.get("account"),
                chat=body.get("chat") or "default", **callbacks,
            )
        except LoginRequiredError:
            self._mark_verified(user_id, provider, False)
            raise
        self._mark_verified(user_id, provider, True)
        return text

    async def _h_api_ask(self, request):
        """Send one prompt through the warm bridge and return the reply.

        Body: ``{"provider", "prompt", "user_id"?, "chat"?, "new_chat"?, "model"?,
        "model_order"?, "account"?}``. Reply: ``{"text", "model", "media"}``.
        """
        body = await self._read_ask(request)
        if isinstance(body, web.Response):
            return body
        used = {"model": None, "media": [], "new_conversation": False}

        def on_model(name):
            used["model"] = name

        def on_media(items):
            used["media"] = items

        def on_new_conversation():
            used["new_conversation"] = True

        try:
            text = await self._run_ask(body, on_model=on_model, on_media=on_media,
                                       on_new_conversation=on_new_conversation)
        except Exception as exc:  # noqa: BLE001 - report any provider failure to the caller
            status, code, message = _failure(exc)
            if code == "failed":
                log.warning("ask failed: %s", exc)
            return web.json_response({"error": code, "message": message}, status=status)
        return web.json_response({"text": text, "model": used["model"], "media": used["media"],
                                  "newConversation": used["new_conversation"]})

    async def _h_api_ask_stream(self, request):
        """The same ask as ``/api/ask``, streamed as newline-delimited JSON.

        Refusals before the reply starts (secret, body, provider) are plain JSON errors,
        as on ``/api/ask``. After that, each line is one event:

        * ``{"type": "status", "label", "elapsedMs"}``: "Working" or "Searching the Web".
          Sent on every change and as a heartbeat every few seconds, so the connection
          never sits idle.
        * ``{"type": "text", "delta"}``: new Markdown for the reply. ``"replace"`` in place
          of ``"delta"`` means the text changed other than by growing: discard what was shown.
        * ``{"type": "done", "text", "model", "media"}``: the final reply, last line.
        * ``{"type": "error", "status", "error", "message"}``: the failure, last line, in
          place of done. ``status`` and ``error`` match ``/api/ask``.
        """
        body = await self._read_ask(request)
        if isinstance(body, web.Response):
            return body
        started = time.monotonic()
        events: asyncio.Queue = asyncio.Queue()
        state = {"label": "Working", "model": None, "media": [], "new_conversation": False}

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        def on_status(label):
            state["label"] = label
            events.put_nowait({"type": "status", "label": label, "elapsedMs": elapsed_ms()})

        def on_text(delta, replace=False):
            events.put_nowait({"type": "text", "replace": delta} if replace else {"type": "text", "delta": delta})

        def on_model(name):
            state["model"] = name

        def on_media(items):
            state["media"] = items

        def on_new_conversation():
            state["new_conversation"] = True

        async def run():
            try:
                text = await self._run_ask(body, on_model=on_model, on_media=on_media,
                                           on_status=on_status, on_text=on_text,
                                           on_new_conversation=on_new_conversation)
                events.put_nowait({"type": "done", "text": text, "model": state["model"], "media": state["media"],
                                   "newConversation": state["new_conversation"]})
            except Exception as exc:  # noqa: BLE001 - report any provider failure to the caller
                status, code, message = _failure(exc)
                if code == "failed":
                    log.warning("ask failed: %s", exc)
                events.put_nowait({"type": "error", "status": status, "error": code, "message": message})
            finally:
                events.put_nowait(None)

        response = web.StreamResponse(headers={
            "Content-Type": "application/x-ndjson",
            # no-transform keeps proxies and gzip from holding the lines back.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        })
        await response.prepare(request)
        events.put_nowait({"type": "status", "label": "Working", "elapsedMs": 0})
        task = asyncio.create_task(run())  # held here so it cannot be collected mid-ask

        connected = True
        while True:
            try:
                event = await asyncio.wait_for(events.get(), timeout=self.STREAM_HEARTBEAT_S)
            except asyncio.TimeoutError:
                event = {"type": "status", "label": state["label"], "elapsedMs": elapsed_ms()}
            if event is None:
                break
            if connected:
                try:
                    await response.write((json.dumps(event) + "\n").encode())
                except ConnectionError:
                    # The browser went away. The ask still finishes, so the warm bridge is
                    # left in a clean state, and the rest of the reply is simply not sent.
                    connected = False
        if connected:
            await response.write_eof()
        return response

    async def _h_api_session(self, request):
        """Whether a user is signed in to a provider. A store lookup only: no browser, no message sent.

        Body: ``{"provider", "user_id"?}``. Reply: ``{"provider", "signed_in"}``.
        """
        if self.api_secret and request.headers.get("X-ChatSherpa-Secret") != self.api_secret:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            return web.json_response({"error": "bad json"}, status=400)
        provider = str(body.get("provider", "")).lower()
        if provider not in REGISTRY:
            return web.json_response({"error": "unknown provider"}, status=400)
        user_id = str(body.get("user_id", "local"))
        session = await self.store.get(user_id, provider)
        stored = bool(session and not session.is_empty())
        # Connected means a stored session has answered an ask. A stored one that has not, or that the
        # provider refused, is not shown as connected.
        signed_in = stored and self._verified.get(f"{user_id}:{provider}") is True
        return web.json_response({"provider": REGISTRY[provider].name, "signed_in": signed_in, "stored": stored})

    def _authorized(self, request) -> bool:
        return not self.api_secret or request.headers.get("X-ChatSherpa-Secret") == self.api_secret

    async def _h_api_models(self, request):
        """GET /api/models?provider=&user_id= -> {"models": [...]}. Reads the model menu of that user's account."""
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        provider = request.query.get("provider", "").lower()
        user_id = request.query.get("user_id", "")
        if provider not in REGISTRY or not user_id:
            return web.json_response({"error": "provider and user_id are required"}, status=400)
        try:
            found = await self.bridge.list_models(provider, user_id)
        except LoginRequiredError as exc:
            return web.json_response({"error": "login_required", "message": str(exc)}, status=401)
        except BotChallengeError as exc:
            return web.json_response({"error": "bot_challenge", "message": str(exc)}, status=503)
        except BrowserUnavailableError as exc:
            return web.json_response({"error": "browser_unavailable", "message": str(exc)}, status=503)
        except ResponseTimeoutError as exc:
            return web.json_response({"error": "timeout", "message": str(exc)}, status=504)
        except Exception as exc:  # noqa: BLE001
            log.warning("models failed: %s", exc)
            return web.json_response({"error": "failed", "message": str(exc)}, status=500)
        return web.json_response({"models": list(found.get("models") or [])})

    async def _h_api_logout(self, request):
        """POST /api/logout {"provider", "user_id"}: forget that user's saved login for one provider."""
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)
        provider = str(body.get("provider", "")).lower()
        user_id = str(body.get("user_id", ""))
        if provider not in REGISTRY or not user_id:
            return web.json_response({"error": "provider and user_id are required"}, status=400)
        name = get_provider(provider).name
        await self.bridge.store.delete(user_id, name)
        await self.bridge.pool.drop(user_id, name)
        await self.bus.emit(Event(EventType.LOGIN_SUCCEEDED, provider=name, user_id=user_id,
                                  data={"via": "logout"}))
        return web.json_response({"ok": True, "provider": name})

    async def _h_connect(self, request):
        token = request.match_info["token"]
        pending = self.broker.get(token)
        if not pending:
            return web.Response(text="This connect link has expired or is invalid.", status=404)
        cmd = (f"chatsherpa grab {pending.provider_cls.name} --cdp http://localhost:9222 "
               f"--token {token} --server {self.public_base_url}")
        html = _CONNECT.read_text("utf-8")
        for a, b in (("__TOKEN__", token), ("__PROVIDER__", pending.provider_cls.name),
                     ("__HOME__", pending.provider_cls.home_url),
                     ("__INGEST__", f"{self.public_base_url}/api/ingest"), ("__CMD__", cmd)):
            html = html.replace(a, b)
        return web.Response(text=html, content_type="text/html")

    async def _h_ingest(self, request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)
        pending = self.broker.get(body.get("token", ""))
        if not pending:
            return web.json_response({"error": "invalid or expired token"}, status=410)
        state = body.get("storage_state")
        if not state and body.get("cookies"):
            from ..session import Session
            state = Session.from_cookies(body["cookies"]).to_storage_state()
            if isinstance(body.get("origins"), list):
                state["origins"] = body["origins"]
        if not state or not state.get("cookies"):
            return web.json_response({"error": "no session data"}, status=400)
        await self.store.put(pending.user_id, pending.provider_cls.name, state)
        home = body.get("home")
        if isinstance(home, str) and pending.provider_cls.entry_url(home):
            await self.store.put_thread(pending.user_id, pending.provider_cls.name, home)
        self.broker.resolve(pending.token, state)
        await self.bus.emit(Event(EventType.LOGIN_SUCCEEDED, provider=pending.provider_cls.name,
                                  user_id=pending.user_id, data={"via": "ingest"}))
        return web.json_response({"ok": True, "provider": pending.provider_cls.name})

    async def _h_options(self, request):
        return web.Response(status=204)

    async def _h_viewer(self, request):
        token = request.match_info["token"]
        pending = self.broker.get(token)
        if not pending:
            return web.Response(text="This login link has expired or is invalid.", status=404)
        ws_base = self.public_base_url.replace("https://", "wss://").replace("http://", "ws://")
        html = _VIEWER.read_text("utf-8")
        html = (html.replace("__WSURL__", f"{ws_base}/ws/{token}")
                    .replace("__PROVIDER__", pending.provider_cls.name)
                    .replace("__HOME__", pending.provider_cls.home_url))
        return web.Response(text=html, content_type="text/html")

    async def _h_ws(self, request):
        token = request.match_info["token"]
        pending = self.broker.get(token)
        ws = web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)
        if not pending or not pending.page:
            await ws.send_json({"type": "error", "message": "expired"})
            await ws.close()
            return ws

        async def send_json(obj):
            if not ws.closed:
                await ws.send_json(obj)

        streamer = LiveStreamer(send_json, pending.context, pending.page)
        await streamer.start()

        async def watch_success():
            try:
                await asyncio.shield(pending.handle.future)
                await send_json({"type": "success"})
            except Exception:
                await send_json({"type": "failed"})
        watcher = asyncio.create_task(watch_success())

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        await streamer.dispatch(json.loads(msg.data))
                    except Exception:
                        pass
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            watcher.cancel()
            await streamer.stop()
        return ws

    @web.middleware
    async def _cors(self, request, handler):
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
        else:
            resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-ChatSherpa-Secret"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    async def _close_bridge(self, _app) -> None:
        await self.bridge.aclose()

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._cors])
        app.on_cleanup.append(self._close_bridge)
        app.add_routes([
            web.get("/healthz", self._h_health),
            web.post("/api/login", self._h_api_login),
            web.post("/api/ingest", self._h_ingest),
            web.post("/api/ask", self._h_api_ask),
            web.post("/api/ask/stream", self._h_api_ask_stream),
            web.post("/api/session", self._h_api_session),
            web.get("/api/models", self._h_api_models),
            web.post("/api/logout", self._h_api_logout),
            web.options("/api/ingest", self._h_options),
            web.get("/connect/{token}", self._h_connect),
            web.get("/login/{token}", self._h_viewer),
            web.get("/ws/{token}", self._h_ws),
        ])
        return app

    def run(self) -> None:
        web.run_app(self.app(), host=self.host, port=self.port, print=None)
