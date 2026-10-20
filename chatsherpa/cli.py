"""chatsherpa — animated command line for ChatSherpa.

    chatsherpa                      banner + command overview
    chatsherpa providers            list providers
    chatsherpa doctor               environment diagnostics
    chatsherpa login <provider>     headful login, saves a session
    chatsherpa ask <provider> "…"   send a prompt, print the reply
    chatsherpa test [all|<p>]       smoke-test provider sessions
    chatsherpa sessions             list stored sessions
    chatsherpa setup                configure the login-server domain
    chatsherpa serve                run the live-view login server
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List

from .base import ProviderConfig
from .client import ChatSherpa
from .config import load_env, resolve
from .providers import REGISTRY, get_provider
from .store import FileSessionStore
from . import ui
from .ui import console

SESS_DIR = "sessions"
SMOKE = "Reply with exactly: ok"


def _env() -> Dict[str, str]:
    e = dict(load_env(".env"))
    e.update({k: v for k, v in os.environ.items() if k in (
        "PUBLIC_BASE_URL", "SERVER_HOST", "SERVER_PORT", "API_SECRET",
        "WEBHOOK_URL", "WEBHOOK_SECRET", "ENCRYPTION_KEY", "CHATSHERPA_HEADLESS")})
    return e


def _store() -> FileSessionStore:
    return FileSessionStore(SESS_DIR, encryption_key=_env().get("ENCRYPTION_KEY") or None)


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
        ("chatsherpa login <provider>", "open a browser, log in once, save the session"),
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
        sess = list(Path(SESS_DIR).glob("*.json*")) if Path(SESS_DIR).exists() else []
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
    creds = resolve(args.provider)
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
                await bridge.login_local(provider_cls.name, email=email, password=password,
                                         headless=args.headless, timeout_s=args.timeout)
            except Exception as exc:
                ui.fail(f"login failed: {exc}")
                return 1
    from rich.panel import Panel
    console.print(Panel(f"[{ui.GOOD}]✓ session saved[/] for [{style}]{provider_cls.name}[/]\n"
                        f"[{ui.SOFT}]try:[/] chatsherpa ask {provider_cls.name} \"hello\"",
                        border_style=ui.GOOD, title="Connected"))
    return 0


async def cmd_ask(args) -> int:
    provider_cls = get_provider(args.provider)
    style = ui.pstyle(provider_cls.name)
    bridge = _bridge(headless=not args.headful)
    bridge.config.response_timeout_ms = args.timeout * 1000
    console.print()
    order = [m.strip() for m in args.model_order.split(",")] if getattr(args, "model_order", None) else None
    try:
        with console.status(f"[{style}]{provider_cls.name} is thinking…", spinner="dots"):
            answer = await bridge.ask(provider_cls.name, "local", args.prompt,
                                      model=getattr(args, "model", None), model_order=order)
    except Exception as exc:
        ui.fail(str(exc))
        return 2
    finally:
        await bridge.aclose()
    from rich.panel import Panel
    from rich.markdown import Markdown
    console.print(Panel(Markdown(answer), title=str(ui.chip(provider_cls.name)),
                        border_style=style, title_align="left"))
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
    await _store().put("local", provider_cls.name, state)
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
        await _store().put("local", provider_cls.name, state)
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
    t.add_column("provider"); t.add_column("user"); t.add_column("age", style=ui.SOFT); t.add_column("enc", style=ui.SOFT)
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
        t.add_row(f"[{ui.pstyle(prov)}]●[/] {prov}", user, age, enc)
    console.print(t)
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
    port = Prompt.ask("Bind port", default=env.get("SERVER_PORT") or "8080")
    api_secret = Prompt.ask("API secret (bots send this to start logins)",
                            default=env.get("API_SECRET") or "")
    updates = {"PUBLIC_BASE_URL": base, "SERVER_HOST": host, "SERVER_PORT": port}
    if api_secret:
        updates["API_SECRET"] = api_secret
    upsert_env(updates)
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
    host = env.get("SERVER_HOST", "0.0.0.0"); port = int(env.get("SERVER_PORT", "8080"))
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
    sub.add_parser("doctor")
    sub.add_parser("sessions")
    sub.add_parser("setup")
    sub.add_parser("serve")

    lg = sub.add_parser("login"); lg.add_argument("provider", choices=provs)
    lg.add_argument("--auto", action="store_true"); lg.add_argument("--headless", action="store_true")
    lg.add_argument("--timeout", type=int, default=300)

    ak = sub.add_parser("ask"); ak.add_argument("provider", choices=provs); ak.add_argument("prompt")
    ak.add_argument("--headful", action="store_true"); ak.add_argument("--timeout", type=int, default=180)
    ak.add_argument("--model", help="preferred model (e.g. Expert, '2.5 Pro')")
    ak.add_argument("--model-order", help="comma-separated model preference order")

    ts = sub.add_parser("test"); ts.add_argument("provider", nargs="?", default="all")
    ts.add_argument("--prompt", default=SMOKE); ts.add_argument("--headful", action="store_true")

    gb = sub.add_parser("grab"); gb.add_argument("provider", choices=provs)
    gb.add_argument("--cdp", help="CDP url of your running Chrome, e.g. http://localhost:9222")
    gb.add_argument("--profile", help="persistent profile dir (when not using --cdp)")
    gb.add_argument("--token", help="connect token (to upload to a login server)")
    gb.add_argument("--server", help="login server base url (with --token)")
    gb.add_argument("--timeout", type=int, default=300)

    im = sub.add_parser("import"); im.add_argument("provider", choices=provs)
    im.add_argument("source", help="cookie JSON file, storageState file, '-' for stdin, or a cookie header with --cookie-header")
    im.add_argument("--cookie-header", action="store_true", help="treat source as a raw 'k=v; k2=v2' string")
    im.add_argument("--domain", help="domain for --cookie-header (default: provider home)")
    return p


SYNC = {"providers": cmd_providers, "doctor": cmd_doctor, "setup": cmd_setup, "serve": cmd_serve}
ASYNC = {"login": cmd_login, "ask": cmd_ask, "test": cmd_test,
         "sessions": cmd_sessions, "import": cmd_import, "grab": cmd_grab}


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
