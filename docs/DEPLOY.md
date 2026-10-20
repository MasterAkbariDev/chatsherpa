# Deploying the live-view login server (adding a domain)

The login server hands users a link whose page **is** a browser running on your
VPS. That link must be served over **HTTPS on a domain** — not a bare IP.

## Why not a bare IP?

- Let's Encrypt (and most CAs) won't issue TLS certificates for a bare IP, so
  you'd be stuck with a self-signed cert → browsers show a scary warning on a
  **login page**. Training users to click past warnings while typing a password
  is unsafe.
- The live view streams the session over a WebSocket; it must be `wss://`,
  which needs real TLS.

So: point a domain (or subdomain) at the box and terminate TLS in front of the
server. `PUBLIC_BASE_URL` is the only thing the library needs — it builds every
link as `{PUBLIC_BASE_URL}/login/{token}`.

## Fastest setup — `chatsherpa setup`

```bash
chatsherpa setup      # asks for your domain, writes .env, prints proxy snippets
chatsherpa serve      # starts the server on SERVER_HOST:SERVER_PORT
```

## Option A — Caddy (automatic HTTPS)

Point an A record (e.g. `login.yourdomain.com`) at the VPS, then:

```caddyfile
# /etc/caddy/Caddyfile
login.yourdomain.com {
    reverse_proxy localhost:8080
}
```

Caddy fetches and renews a Let's Encrypt cert automatically. Set
`PUBLIC_BASE_URL=https://login.yourdomain.com`.

## Option B — Cloudflare Tunnel (no open ports, no DNS work)

```bash
cloudflared tunnel --url http://localhost:8080
```

It prints an `https://<random>.trycloudflare.com` URL with TLS already handled.
Set that as `PUBLIC_BASE_URL`. (For a stable hostname, create a named tunnel
bound to a domain in your Cloudflare account.)

## Option C — nginx + certbot

```nginx
server {
    server_name login.yourdomain.com;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;   # WebSocket for the live view
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

```bash
certbot --nginx -d login.yourdomain.com
```

## How a bot starts a login

Your bot (any host, any framework) POSTs to the server and forwards the link:

```bash
curl -X POST https://login.yourdomain.com/api/login \
  -H "X-ChatSherpa-Secret: $API_SECRET" \
  -d '{"provider":"chatgpt","user_id":42}'
# → {"token":"…","url":"https://login.yourdomain.com/login/…","expires_in":600}
```

The user opens `url`, logs in (password, Google/OAuth, 2FA all work), and the
session is caught, stored (encrypted if `ENCRYPTION_KEY` is set), and a
`login_succeeded` event fires — wire it to a webhook or callback to DM the user.

## Relevant `.env` keys

```
PUBLIC_BASE_URL=https://login.yourdomain.com
SERVER_HOST=0.0.0.0
SERVER_PORT=8080
API_SECRET=<shared secret bots send>
WEBHOOK_URL=https://yourhost/ai-errors      # optional
WEBHOOK_SECRET=<hmac secret>                 # optional
ENCRYPTION_KEY=<fernet key>                   # optional, encrypts sessions at rest
```

Generate an encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
