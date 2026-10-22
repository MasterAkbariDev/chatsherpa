"""chatsherpa — animated command line for ChatSherpa.

    chatsherpa                      banner + command overview
    chatsherpa providers            list providers
    chatsherpa models [provider]    list model names for --model
    chatsherpa doctor               environment diagnostics
    chatsherpa login <provider>     headful login, saves a session
    chatsherpa ask <provider> "…"   send a prompt, print the reply
    chatsherpa test [all|<p>]       smoke-test provider sessions
    chatsherpa sessions             list stored sessions
    chatsherpa setup                configure the login-server domain
    chatsherpa link <provider>      create a login link from API_SECRET in .env
    chatsherpa serve                run the live-view login server
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from .base import ProviderConfig
from .client import ChatSherpa
from .config import app_home, load_env, resolve
from .providers import REGISTRY, get_provider
from .store import FileSessionStore
from . import ui
from .ui import console

SMOKE = "Reply with exactly: ok"


def _home() -> Path:
    return app_home()


def _env_path() -> Path:
    return _home() / ".env"


def _sessions_dir() -> Path:
    return _home() / "sessions"


def _env() -> Dict[str, str]:
    e = dict(load_env(str(_env_path())))
    e.update({k: v for k, v in os.environ.items() if k in (
        "PUBLIC_BASE_URL", "SERVER_HOST", "SERVER_PORT", "API_SECRET", "API_KEY",
        "WEBHOOK_URL", "WEBHOOK_SECRET", "ENCRYPTION_KEY", "CHATSHERPA_HEADLESS")})
    return e


def _store() -> FileSessionStore:
    return FileSessionStore(_sessions_dir(), encryption_key=_env().get("ENCRYPTION_KEY") or None)


def _bridge(headless: bool = True) -> ChatSherpa:
    return ChatSherpa(store=_store(), headless=headless)


def upsert_env(updates: Dict[str, str], path: str = ".env") -> None:
    lines = Path(path).read_text("utf-8").splitlines() if Path(path).exists() else []
    seen, out = set(), []
    for ln in lines:
        m = re.match(r"^(\w+)=", ln)
        if m and m.group(1) in updates:
            out.append(f"{m.group(1)}={updates[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(ln)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    Path(path).write_text("\n".join(out) + "\n", "utf-8")


# ----------------------------- commands ----------------------------- #
def cmd_overview() -> int:
    ui.banner()
    from rich.table import Table
    from rich import box
    t = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    t.add_column("command", style="bold")
    t.add_column("what it does", style=ui.SOFT)
    rows = [
        ("chatsherpa setup", "configure your login-server domain (+ reverse-proxy help)"),
        ("chatsherpa link <provider>", "create a login link (API_SECRET from .env)"),
        ("chatsherpa login <provider>", "open a browser, log in once, save the session"),
        ("chatsherpa models [provider]", "read model names from the signed-in account"),
        ("chatsherpa ask <provider> \"…\"", "send a prompt and print the reply"),
        ("chatsherpa test [all]", "smoke-test stored sessions"),
        ("chatsherpa sessions", "list saved sessions"),
        ("chatsherpa serve", "run the live-view login server (bots use this)"),
        ("chatsherpa doctor", "check your environment"),
    ]
    for c, d in rows:
        t.add_row(c, d)
    console.print(t)
    console.print(f"[{ui.SOFT}]providers:[/]  " +
                  "  ".join(f"[{ui.pstyle(p)}]●[/] {p}" for p in sorted(REGISTRY)))
    return 0


async def cmd_models(args) -> int:
    from rich.table import Table
    from rich import box
    names = [args.provider] if getattr(args, "provider", None) else sorted(REGISTRY)
    account = getattr(args, "account", None)
    bridge = _bridge(headless=not getattr(args, "headful", False))
    rows = []
    example = None
    try:
        for name in names:
            style = ui.pstyle(name)
            try:
                with console.status(f"[{style}]Reading {name} models…", spinner="dots"):
                    found = await bridge.list_models(
                        name, "local", account=account if name == "gemini" else None)
            except Exception as exc:
                rows.append((name, style, str(exc)))
                continue
            labels = found.get("models") or []
            used = found.get("account")
            who = f" u/{used}" if used is not None else ""
            shown = ", ".join(labels) if labels else "no model picker"
            rows.append((f"{name}{who}", style, shown))
            if labels and example is None:
                example = (name, labels[0])
    finally:
        await bridge.aclose()
    from rich.markup import escape
    t = Table(title="Models", box=box.ROUNDED, title_style=f"bold {ui.TEAL}")
    t.add_column("provider")
    t.add_column("pass to --model", style=ui.SOFT)
    for name, style, shown in rows:
        t.add_row(f"[{style}]●[/] {escape(name)}", escape(shown))
    console.print(t)
    if example:
        console.print(f"[{ui.SOFT}]example:[/] chatsherpa ask {example[0]} \"hello\" --model \"{example[1]}\"")
    return 0


def cmd_providers() -> int:
    from rich.table import Table
    from rich import box
    t = Table(title="Providers", box=box.ROUNDED, title_style=f"bold {ui.TEAL}")
    t.add_column("provider"); t.add_column("url", style=ui.SOFT)
    for name in sorted(REGISTRY):
        t.add_row(f"[{ui.pstyle(name)}]●[/] {name}", REGISTRY[name].home_url)
    console.print(t)
    return 0


def cmd_doctor() -> int:
    from rich.table import Table
    from rich import box
    checks: List = []
    with console.status("[bold]Running diagnostics…", spinner="dots"):
        time.sleep(.25)
        checks.append(("Python ≥ 3.9", "ok" if sys.version_info >= (3, 9) else "fail",
                       ".".join(map(str, sys.version_info[:3]))))
        try:
            import playwright  # noqa
            checks.append(("Playwright", "ok", getattr(playwright, "__version__", "installed")))
        except Exception:
            checks.append(("Playwright", "fail", "pip install playwright"))
        cache = Path.home() / "Library/Caches/ms-playwright"
        alt = Path.home() / ".cache/ms-playwright"
        found = any((c.exists() and any(c.glob("chromium*"))) for c in (cache, alt))
        checks.append(("Chromium browser", "ok" if found else "warn",
                       "installed" if found else "run: playwright install chromium"))
        for mod, label, extra in (("aiohttp", "Login server", "[server]"),
                                   ("cryptography", "Encryption at rest", "[crypto]")):
            try:
                __import__(mod)
                checks.append((label, "ok", "available"))
            except Exception:
                checks.append((label, "warn", f"optional — pip install 'chatsherpa{extra}'"))
        env = _env()
        checks.append(("Login domain (PUBLIC_BASE_URL)", "ok" if env.get("PUBLIC_BASE_URL") else "warn",
                       env.get("PUBLIC_BASE_URL") or "run: chatsherpa setup"))
        sess_dir = _sessions_dir()
        sess = list(sess_dir.glob("*.json*")) if sess_dir.exists() else []
        checks.append(("Saved sessions", "ok" if sess else "warn",
                       f"{len(sess)} found" if sess else "run: chatsherpa login <provider>"))

    t = Table(box=box.SIMPLE, show_edge=False)
    t.add_column(""); t.add_column("check"); t.add_column("detail", style=ui.SOFT)
    icon = {"ok": f"[{ui.GOOD}]✓[/]", "warn": f"[{ui.AMBER}]![/]", "fail": f"[{ui.CORAL}]✗[/]"}
    for name, st, detail in checks:
        t.add_row(icon[st], name, detail)
    console.print(t)
    return 1 if any(s == "fail" for _, s, _ in checks) else 0


async def cmd_login(args) -> int:
    provider_cls = get_provider(args.provider)
    creds = resolve(args.provider, env_path=str(_env_path()))
    email = creds.email if args.auto else None
    password = creds.password if args.auto else None
    bridge = _bridge(headless=args.headless)
    style = ui.pstyle(provider_cls.name)
    console.print()
    with console.status(f"[{style}]Launching browser for {provider_cls.name}…", spinner="dots12"):
        msg = f"[bold]A browser window is open.[/] Log in to [{style}]{provider_cls.name}[/] "
        msg += "(Google / 2FA all work)."
        console.print(msg)
        with console.status(f"[{style}]Waiting for login — catching session…", spinner="aesthetic"):
            try:
                await bridge.login_local(provider_cls.name, user_id=getattr(args, "user", None) or "local",
                                         email=email, password=password,
                                         headless=args.headless, timeout_s=args.timeout)
            except Exception as exc:
                ui.fail(f"login failed: {exc}")
                return 1
    from rich.panel import Panel
    console.print(Panel(f"[{ui.GOOD}]✓ session saved[/] for [{style}]{provider_cls.name}[/]\n"
                        f"[{ui.SOFT}]try:[/] chatsherpa ask {provider_cls.name} \"hello\"",
                        border_style=ui.GOOD, title="Connected"))
    return 0


def _ask_via_server(args, chat: str, order) -> Optional[dict]:
    """Send the prompt to the running login server, which keeps its browser warm.

    Returns the server's JSON reply. Returns ``None`` when no server is listening
    or it has no ``/api/ask`` route (an older install), so the caller runs locally.
    Once the request has been sent it is never retried locally, so a prompt is
    never sent twice.
    """
    import urllib.error
    import urllib.request
    env = _env()
    base = f"http://127.0.0.1:{env.get('SERVER_PORT', '8091')}"
    headers = {"Content-Type": "application/json"}
    if env.get("API_SECRET"):
        headers["X-ChatSherpa-Secret"] = env["API_SECRET"]
    try:
        urllib.request.urlopen(urllib.request.Request(f"{base}/healthz", headers=headers), timeout=1).close()
    except Exception:
        return None
    body = {"provider": args.provider, "prompt": args.prompt,
            "user_id": getattr(args, "user", None) or "local", "chat": chat,
            "new_chat": bool(getattr(args, "new", False)), "model": getattr(args, "model", None),
            "model_order": order, "account": getattr(args, "account", None)}
    req = urllib.request.Request(f"{base}/api/ask", data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=args.timeout + 30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            return None if exc.code == 404 else {"error": "http", "message": f"HTTP {exc.code}"}
        return payload if isinstance(payload, dict) and "error" in payload else {"error": "http", "message": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"error": "unreachable", "message": f"login server did not answer: {exc}"}


def _print_answer(name: str, answer: str, model: Optional[str], elapsed: float) -> None:
    from rich.panel import Panel
    from rich.markdown import Markdown
    style = ui.pstyle(name)
    label = f"{name} · {model}" if model else name
    subtitle = f"[{ui.SOFT}]{label} · {elapsed:.1f}s[/]"
    console.print(Panel(Markdown(answer, code_theme="monokai"), title=str(ui.chip(name)),
                        subtitle=subtitle, subtitle_align="right",
                        border_style=style, title_align="left"))


async def cmd_ask(args) -> int:
    provider_cls = get_provider(args.provider)
    style = ui.pstyle(provider_cls.name)
    console.print()
    order = [m.strip() for m in args.model_order.split(",")] if getattr(args, "model_order", None) else None
    chat = getattr(args, "chat", None) or "default"
    t0 = time.monotonic()
    if not getattr(args, "local", False):
        with console.status(f"[{style}]{provider_cls.name} is thinking…", spinner="dots"):
            remote = await asyncio.to_thread(_ask_via_server, args, chat, order)
        if remote is not None:
            if remote.get("error"):
                ui.fail(remote.get("message") or remote["error"])
                return 2
            _print_answer(provider_cls.name, remote.get("text", ""), remote.get("model"),
                          time.monotonic() - t0)
            return 0
    bridge = _bridge(headless=not args.headful)
    bridge.config.response_timeout_ms = args.timeout * 1000
    used_model = {"name": None}
    try:
        with console.status(f"[{style}]{provider_cls.name} is thinking…", spinner="dots") as status:
            def on_model(m):
                used_model["name"] = m
                status.update(f"[{style}]{provider_cls.name}[/] [{ui.SOFT}]·[/] [{style}]{m}[/] is thinking…")
            answer = await bridge.ask(provider_cls.name, getattr(args, "user", None) or "local", args.prompt,
                                      model=getattr(args, "model", None), model_order=order,
                                      new_chat=getattr(args, "new", False),
                                      account=getattr(args, "account", None),
                                      on_model=on_model,
                                      chat=getattr(args, "chat", None) or "default")
    except Exception as exc:
        ui.fail(str(exc))
        await bridge.aclose()
        return 2
    _print_answer(provider_cls.name, answer, used_model["name"], time.monotonic() - t0)
    await bridge.aclose()
    return 0


async def cmd_chats(args) -> int:
    """List the named chats saved for one user and provider."""
    from rich.table import Table
    from rich import box
    provider_cls = get_provider(args.provider)
    user = getattr(args, "user", None) or "local"
    chats = await _store().list_threads(user, provider_cls.name)
    if not chats:
        ui.warn(f"no saved chats for {user} on {provider_cls.name} yet — chatsherpa ask {provider_cls.name} \"hi\"")
        return 0
    t = Table(box=box.SIMPLE, show_edge=False)
    t.add_column("chat"); t.add_column("url", style=ui.SOFT)
    for name, url in sorted(chats.items()):
        t.add_row(f"[{ui.pstyle(provider_cls.name)}]●[/] {name}", url)
    console.print(t)
    return 0


async def cmd_test(args) -> int:
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.table import Table
    from rich import box
    names = sorted(REGISTRY) if args.provider == "all" else [args.provider.lower()]
    results: Dict[str, tuple] = {}

    async def one(name):
        bridge = _bridge(headless=not args.headful)
        try:
            if not await bridge.has_session(name):
                return name, "skip", "no session — chatsherpa login " + name
            ans = (await bridge.ask(name, "local", args.prompt)).strip().replace("\n", " ")
            return name, "pass", ans[:80]
        except Exception as exc:
            return name, "fail", f"{type(exc).__name__}: {exc}"
        finally:
            await bridge.aclose()

    console.print()
    with Progress(SpinnerColumn(style=ui.TEAL), TextColumn("[progress.description]{task.description}"),
                  BarColumn(complete_style=ui.TEAL), console=console, transient=True) as prog:
        task = prog.add_task("testing providers", total=len(names))
        for coro in asyncio.as_completed([one(n) for n in names]):
            name, st, detail = await coro
            results[name] = (st, detail)
            prog.update(task, advance=1, description=f"tested {name}")

    t = Table(box=box.SIMPLE, show_edge=False)
    t.add_column(""); t.add_column("provider"); t.add_column("result", style=ui.SOFT)
    icon = {"pass": f"[{ui.GOOD}]PASS[/]", "fail": f"[{ui.CORAL}]FAIL[/]", "skip": f"[{ui.AMBER}]SKIP[/]"}
    passed = 0
    for name in names:
        st, detail = results.get(name, ("fail", "no result"))
        passed += st == "pass"
        t.add_row(icon[st], f"[{ui.pstyle(name)}]●[/] {name}", detail)
    console.print(t)
    console.print(f"[{ui.SOFT}]{passed}/{len(names)} passed[/]")
    return 0 if passed == len(names) else 1


async def cmd_import(args) -> int:
    """Import a session from a browser-cookie export (Cookie-Editor JSON,
    a Playwright storageState JSON, or a raw 'k=v; k2=v2' cookie header)."""
    from .session import Session
    provider_cls = get_provider(args.provider)
    style = ui.pstyle(provider_cls.name)
    src = args.source
    try:
        if args.cookie_header:
            domain = args.domain or provider_cls.home_url
            session = Session.from_cookie_string(src, domain)
        elif src == "-":
            session = Session.coerce(__import__("json").loads(sys.stdin.read()))
        else:
            session = Session.coerce(src)
    except Exception as exc:
        ui.fail(f"could not read session source: {exc}")
        return 2
    state = session.to_storage_state()
    n = len(state.get("cookies", []))
    if not n:
        ui.fail("no usable cookies found in that source")
        return 2
    await _store().put(getattr(args, "user", None) or "local", provider_cls.name, state)
    from rich.panel import Panel
    console.print(Panel(f"[{ui.GOOD}]✓ imported {n} cookies[/] for [{style}]{provider_cls.name}[/]\n"
                        f"[{ui.SOFT}]try:[/] chatsherpa ask {provider_cls.name} \"hello\"",
                        border_style=ui.GOOD, title="Imported"))
    return 0


async def cmd_grab(args) -> int:
    """Grab a session from the user's real Chrome (avoids Google's automation block)."""
    from .login.grab import grab_session, upload_session
    provider_cls = get_provider(args.provider)
    style = ui.pstyle(provider_cls.name)
    console.print()
    hint = ("connecting to your Chrome" if args.cdp
            else "launching your installed Chrome — log in in the window")
    try:
        with console.status(f"[{style}]{hint}; waiting for login…", spinner="dots12"):
            state = await grab_session(provider_cls, cdp=args.cdp, profile=args.profile,
                                       timeout_s=args.timeout)
    except Exception as exc:
        ui.fail(f"grab failed: {exc}")
        if not args.cdp:
            console.print(f"[{ui.SOFT}]tip: start Chrome with a debug port and use --cdp:[/]\n"
                          f"  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome "
                          f"--remote-debugging-port=9222")
        return 1
    n = len(state.get("cookies", []))
    from rich.panel import Panel
    if args.token and args.server:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, upload_session, f"{args.server.rstrip('/')}/api/ingest",
                args.token, provider_cls.name, state)
        except Exception as exc:
            ui.fail(f"captured {n} cookies but upload failed: {exc}")
            return 1
        console.print(Panel(f"[{ui.GOOD}]✓ captured {n} cookies and uploaded[/] for "
                            f"[{style}]{provider_cls.name}[/]", border_style=ui.GOOD, title="Connected"))
    else:
        await _store().put(getattr(args, "user", None) or "local", provider_cls.name, state)
        console.print(Panel(f"[{ui.GOOD}]✓ captured {n} cookies[/] for [{style}]{provider_cls.name}[/]\n"
                            f"[{ui.SOFT}]try:[/] chatsherpa ask {provider_cls.name} \"hello\"",
                            border_style=ui.GOOD, title="Connected"))
    return 0


async def cmd_sessions(args) -> int:
    from rich.table import Table
    from rich import box
    store = _store()
    rows = await store.list()
    t = Table(title="Stored sessions", box=box.ROUNDED, title_style=f"bold {ui.TEAL}")
    t.add_column("provider"); t.add_column("account"); t.add_column("chats", style=ui.SOFT)
    t.add_column("age", style=ui.SOFT); t.add_column("enc", style=ui.SOFT)
    if not rows:
        console.print(f"[{ui.SOFT}]no sessions yet — run[/] chatsherpa login <provider>")
        return 0
    now = time.time()
    for user, prov in sorted(rows):
        p = store.path_for(user, prov)
        age = "?"
        if p.exists():
            secs = now - p.stat().st_mtime
            age = f"{int(secs//3600)}h" if secs >= 3600 else f"{int(secs//60)}m"
        enc = "🔒" if p.suffix == ".enc" else "—"
        chats = await store.list_threads(user, prov)
        t.add_row(f"[{ui.pstyle(prov)}]●[/] {prov}", user, str(len(chats)) if chats else "—", age, enc)
    console.print(t)
    return 0


async def cmd_remove(args) -> int:
    """Delete a stored session (token) for a provider, per account or all."""
    store = _store()
    provider = get_provider(args.provider).name
    rows = await store.list()
    label = getattr(args, "user", None) or "local"
    if args.all:
        targets = sorted({u for (u, p) in rows if p == provider})
    else:
        targets = [label] if (label, provider) in rows else []
    if not targets:
        ui.warn(f"no stored session for {provider}"
                + ("" if args.all else f" (account '{label}')"))
        return 1
    for user in targets:
        await store.delete(user, provider)
        ui.ok(f"removed {provider} (account '{user}')")
    return 0


def request_login_link(base_url: str, api_secret: str, provider: str,
                       user_id: str = "local", mode: str = "connect") -> dict:
    """POST /api/login and return the server's JSON (includes ``url``)."""
    body = json.dumps({"provider": provider, "user_id": user_id, "mode": mode}).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/login",
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "chatsherpa",
            "X-ChatSherpa-Secret": api_secret,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_link(args) -> int:
    """Create a connect (or live) login link using the secret already in .env."""
    from rich.panel import Panel
    env = _env()
    base = (env.get("PUBLIC_BASE_URL") or "").rstrip("/")
    secret = env.get("API_SECRET") or env.get("API_KEY")
    if not base:
        ui.fail("no PUBLIC_BASE_URL. Run: chatsherpa setup")
        return 1
    if not secret:
        ui.fail("no API_SECRET (or API_KEY) in the environment or .env")
        return 1
    mode = "live" if args.live else "connect"
    try:
        info = request_login_link(base, secret, args.provider, args.user, mode)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:180]
        ui.fail(f"login server returned {exc.code}: {detail}")
        return 1
    except Exception as exc:
        ui.fail(f"could not create a link: {exc}")
        return 1
    url = info.get("url")
    if not url:
        ui.fail("the server did not return a link")
        return 1
    console.print(Panel(
        f"[{ui.TEAL}]{url}[/]\n[{ui.SOFT}]{mode} · user {args.user} · expires in {info.get('expires_in', '?')}s[/]",
        title=f"login link · {args.provider}", border_style=ui.TEAL))
    return 0


def cmd_setup(args) -> int:
    from rich.prompt import Prompt
    from rich.panel import Panel
    ui.banner("let's configure your login-server domain")
    env = _env()
    console.print(f"[{ui.SOFT}]The login link is built from a public HTTPS domain. Avoid a bare IP — "
                  f"certificates for IPs aren't issued, so browsers warn on a login page.[/]\n")
    base = Prompt.ask("Public base URL ([bold]https://[/] + domain)",
                      default=env.get("PUBLIC_BASE_URL") or "https://login.example.com")
    host = Prompt.ask("Bind host", default=env.get("SERVER_HOST") or "0.0.0.0")
    port = Prompt.ask("Bind port", default=env.get("SERVER_PORT") or "8091")
    api_secret = Prompt.ask("API secret (bots send this to start logins)",
                            default=env.get("API_SECRET") or "")
    updates = {"PUBLIC_BASE_URL": base, "SERVER_HOST": host, "SERVER_PORT": port}
    if api_secret:
        updates["API_SECRET"] = api_secret
    upsert_env(updates, path=str(_env_path()))
    ui.ok("wrote PUBLIC_BASE_URL and server settings to .env")

    if base.startswith("http://") or re.match(r"https?://\d+\.\d+\.\d+\.\d+", base):
        ui.warn("that looks like plain HTTP or a bare IP — use HTTPS on a domain for a login page.")

    domain = re.sub(r"^https?://", "", base).split("/")[0]
    caddy = (f"# /etc/caddy/Caddyfile  — auto HTTPS via Let's Encrypt\n"
             f"{domain} {{\n    reverse_proxy {host if host!='0.0.0.0' else 'localhost'}:{port}\n}}")
    cloud = (f"# No open ports / DNS needed — tunnel to a Cloudflare domain:\n"
             f"cloudflared tunnel --url http://localhost:{port}\n"
             f"# then set PUBLIC_BASE_URL to the https://… URL it prints")
    console.print(Panel(caddy, title="Option A · Caddy reverse proxy", border_style=ui.TEAL))
    console.print(Panel(cloud, title="Option B · Cloudflare Tunnel (quick start)", border_style=ui.VIOLET))
    console.print(f"\n[{ui.GOOD}]✓[/] next: [bold]chatsherpa serve[/]  (then your bot POSTs to {base}/api/login)")
    return 0


def cmd_serve(args) -> int:
    env = _env()
    base = env.get("PUBLIC_BASE_URL")
    if not base:
        ui.warn("no PUBLIC_BASE_URL set. Run: chatsherpa setup")
        return 2
    try:
        from .login.server import LoginServer
    except Exception as exc:
        ui.fail(f"server deps missing ({exc}). Install: pip install 'chatsherpa[server]'")
        return 2
    from .events import EventBus, WebhookSink
    from rich.panel import Panel
    bus = EventBus()
    if env.get("WEBHOOK_URL"):
        bus.on(WebhookSink(env["WEBHOOK_URL"], secret=env.get("WEBHOOK_SECRET")))
    host = env.get("SERVER_HOST", "0.0.0.0"); port = int(env.get("SERVER_PORT", "8091"))
    server = LoginServer(store=_store(), public_base_url=base, bus=bus,
                         headless=True, api_secret=env.get("API_SECRET"),
                         host=host, port=port)
    ui.banner("live-view login server")
    console.print(Panel(
        f"public url   [{ui.TEAL}]{base}[/]\n"
        f"listening    {host}:{port}\n"
        f"start login  POST {base}/api/login  {{\"provider\":\"chatgpt\",\"user_id\":42}}\n"
        f"health       {base}/healthz",
        title="serving", border_style=ui.TEAL))
    console.print(f"[{ui.SOFT}]Ctrl-C to stop.[/]")
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    return 0


# ----------------------------- parser ----------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chatsherpa", add_help=True, description="ChatSherpa CLI")
    sub = p.add_subparsers(dest="command")
    provs = sorted(REGISTRY)

    sub.add_parser("providers")
    md = sub.add_parser("models", help="read model names from the signed-in account")
    md.add_argument("provider", nargs="?", choices=provs)
    md.add_argument("--account", type=int, help="Gemini account slot, 0 is the first signed-in account")
    md.add_argument("--headful", action="store_true")
    sub.add_parser("doctor")
    sub.add_parser("sessions")
    sub.add_parser("setup")
    sub.add_parser("serve")

    ln = sub.add_parser("link", help="create a login link using API_SECRET from .env")
    ln.add_argument("provider", choices=provs)
    ln.add_argument("--user", default="local", help="user id the session is stored under")
    ln.add_argument("--live", action="store_true", help="server browser instead of connect mode")

    lg = sub.add_parser("login"); lg.add_argument("provider", choices=provs)
    lg.add_argument("--auto", action="store_true"); lg.add_argument("--headless", action="store_true")
    lg.add_argument("--user", default="local", help="account label to store under (default: local)")
    lg.add_argument("--timeout", type=int, default=300)

    ak = sub.add_parser("ask"); ak.add_argument("provider", choices=provs); ak.add_argument("prompt")
    ak.add_argument("--headful", action="store_true"); ak.add_argument("--timeout", type=int, default=180)
    ak.add_argument("--local", action="store_true",
                    help="run the browser in this process instead of the running login server")
    ak.add_argument("--user", default="local", help="account label to use (default: local)")
    ak.add_argument("--chat", default="default", metavar="NAME",
                    help="named chat to continue for this user (default: 'default'); a new name starts a new chat")
    ak.add_argument("--new", action="store_true", help="reset the named chat and start it over")
    ak.add_argument("--account", type=int, help="Gemini account slot: 0 is the main Google account, 1 the next")
    ak.add_argument("--model", help="preferred model (e.g. Expert, '3.1 Pro')")
    ak.add_argument("--model-order", help="comma-separated model preference order")

    ts = sub.add_parser("test"); ts.add_argument("provider", nargs="?", default="all")
    ts.add_argument("--prompt", default=SMOKE); ts.add_argument("--headful", action="store_true")

    gb = sub.add_parser("grab"); gb.add_argument("provider", choices=provs)
    gb.add_argument("--cdp", help="CDP url of your running Chrome, e.g. http://localhost:9222")
    gb.add_argument("--profile", help="persistent profile dir (when not using --cdp)")
    gb.add_argument("--token", help="connect token (to upload to a login server)")
    gb.add_argument("--server", help="login server base url (with --token)")
    gb.add_argument("--user", default="local", help="account label to store under (default: local)")
    gb.add_argument("--timeout", type=int, default=300)

    im = sub.add_parser("import"); im.add_argument("provider", choices=provs)
    im.add_argument("source", help="cookie JSON file, storageState file, '-' for stdin, or a cookie header with --cookie-header")
    im.add_argument("--cookie-header", action="store_true", help="treat source as a raw 'k=v; k2=v2' string")
    im.add_argument("--domain", help="domain for --cookie-header (default: provider home)")
    im.add_argument("--user", default="local", help="account label to store under (default: local)")

    ch = sub.add_parser("chats", help="list a user's saved chats for a provider")
    ch.add_argument("provider", choices=provs)
    ch.add_argument("--user", default="local", help="account label (default: local)")

    rm = sub.add_parser("remove", help="delete a stored session (token) for a provider/account")
    rm.add_argument("provider", choices=provs)
    rm.add_argument("--user", default="local", help="account label to remove (default: local)")
    rm.add_argument("--all", action="store_true", help="remove every stored account for this provider")
    return p


SYNC = {"providers": cmd_providers, "doctor": cmd_doctor, "setup": cmd_setup,
        "serve": cmd_serve, "link": cmd_link}
ASYNC = {"login": cmd_login, "ask": cmd_ask, "test": cmd_test,
         "sessions": cmd_sessions, "import": cmd_import, "grab": cmd_grab,
         "models": cmd_models, "remove": cmd_remove, "chats": cmd_chats}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.command:
        return cmd_overview()
    if args.command in SYNC:
        return SYNC[args.command]() if args.command in ("providers", "doctor") else SYNC[args.command](args)
    if args.command in ASYNC:
        try:
            return asyncio.run(ASYNC[args.command](args))
        except KeyboardInterrupt:
            ui.warn("cancelled")
            return 130
    return cmd_overview()


if __name__ == "__main__":
    sys.exit(main())
