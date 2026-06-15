# GEO-36 — Host security & TLS

**Decision: terminate TLS at an optional Caddy edge (`docker-compose.tls.yml`), not certbot+nginx.**
Caddy folds certificate issuance, **auto-renewal**, OCSP stapling, the HTTP→HTTPS redirect, and
HSTS into the server itself — the fewest moving parts and nothing to forget to renew. The existing
`web` nginx keeps running unprivileged on `:8080` and is no longer published to the host when the
edge is in front; Caddy is the only public entrypoint. App security headers (CSP, nosniff,
Referrer-Policy, X-Frame-Options) live in `web/nginx.conf` so they apply **with or without** the
edge; **HSTS is set only at Caddy**, where TLS actually terminates. Host hardening (firewall, SSH,
unattended security upgrades, non-root containers) ships as idempotent scripts in `deploy/`.

## Why Caddy over certbot + nginx

| Concern | Caddy edge (chosen) | certbot + nginx edge |
|---|---|---|
| Cert issuance | Built in (ACME on first request) | Separate `certbot` run + webroot/ACME volume shared with nginx |
| **Renewal** | Automatic, in-process, no cron | `certbot renew` timer **+ an nginx reload hook** to pick up the new cert |
| Redirect 80→443 | Automatic | Hand-written server block |
| HSTS / OCSP stapling | One header line / automatic | Manual directives |
| HTTP/2 + HTTP/3 | Default | nginx needs explicit `http2`; HTTP/3 needs a custom build |
| Moving parts | 1 container + 1 config | nginx edge + certbot + timer + shared volume + reload hook |

certbot is the right call when an org already standardises on it; for this single-stack VPS the
renewal timer and reload hook are exactly the "things you forget" that expire a cert at 3 a.m. Caddy
removes them. (A certbot+nginx recipe is documented as the fallback at the bottom.)

## How a deployer enables TLS

```bash
# Public DNS name → this host, ports 80/443 open (see deploy/firewall.sh).
SITE_ADDRESS=sites.example.com ACME_EMAIL=ops@example.com \
  docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d

# Smoke-test without public DNS: SITE_ADDRESS defaults to `localhost` → Caddy local self-signed CA.
docker compose -f docker-compose.yml -f docker-compose.tls.yml config   # validate the merge first
```

`docker-compose.tls.yml` adds the `caddy` service (ports 80/443 + 443/udp for HTTP/3), mounts
`deploy/Caddyfile` read-only, persists issued certs in the `caddy_data` volume (so renewals survive
restarts), and `!reset`s `web.ports` so `:8080` is no longer reachable from the host — the edge is
the only public door. The edge waits on `web`'s healthcheck before starting.

## Security headers

Set in `web/nginx.conf` (apply on every deployment, edge or not), HSTS added at the Caddy edge:

| Header | Value | Where |
|---|---|---|
| `Content-Security-Policy` | see below | nginx (server + asset/index locations) |
| `X-Content-Type-Options` | `nosniff` | nginx (all locations) |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | nginx |
| `X-Frame-Options` | `DENY` (+ CSP `frame-ancestors 'none'`) | nginx |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` | **Caddy only** (TLS terminates there) |

The CSP is tuned so it does **not** break MapLibre + deck.gl:

```
default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self';
script-src 'self';
worker-src blob:; child-src blob:;          # MapLibre spins web workers up from blob: URLs
style-src 'self' 'unsafe-inline';           # MapLibre/deck.gl set inline element styles
img-src 'self' data: blob: https://api.mapbox.com https://*.tiles.mapbox.com;
font-src 'self' data:;
connect-src 'self' https://api.mapbox.com https://*.tiles.mapbox.com https://events.mapbox.com
```

Same-origin `/assets`, `/api`, and `/data/*.pmtiles` are covered by `'self'`. Mapbox domains are
allowlisted unconditionally: the SPA bakes `MAPBOX_TOKEN` at build time (the config is static — the
runtime image does no env-substitution on this file), and the entries are inert when the token-less
CARTO basemap is used. Note the nginx `add_header` inheritance gotcha — it **replaces**, not merges,
so the locations that set `Cache-Control` (`= /index.html`, `/assets/`) re-state the header set; the
bootstrapping document (`/` fallback) inherits it from the server block.

## Host hardening baseline (`deploy/`)

| Artifact | What it does |
|---|---|
| `deploy/firewall.sh` | ufw: default-deny inbound, allow 80/443(+udp), SSH locked to `SSH_ALLOW_FROM` CIDR or rate-limited. Idempotent. |
| `deploy/sshd_hardening.conf` | sshd drop-in: `PasswordAuthentication no`, `PermitRootLogin prohibit-password`, `KbdInteractiveAuthentication no`, `MaxAuthTries 3`, idle-timeout. |
| `deploy/setup-unattended-upgrades.sh` | Debian/Ubuntu `unattended-upgrades`: daily, scoped to the **security** pocket, auto-reboot 04:00. |
| `deploy/verify-nonroot.sh` | Asserts each image runs non-root with the expected uid (api 10002, web 101, ingest 10001, frontend 10003). |

Containers are already non-root (each Dockerfile declares a uid + `HEALTHCHECK`); `verify-nonroot.sh`
guards against a regression. The Caddy edge runs as root **by design** — it binds privileged ports
80/443 — and is excluded from the non-root assertion.

```bash
sudo SSH_ALLOW_FROM=203.0.113.0/24 ./deploy/firewall.sh
sudo install -m 0644 deploy/sshd_hardening.conf /etc/ssh/sshd_config.d/10-geo-hardening.conf
sudo sshd -t && sudo systemctl reload ssh          # validate FIRST; keep a second session open
sudo ./deploy/setup-unattended-upgrades.sh
./deploy/verify-nonroot.sh
```

## Deployer checklist

- [ ] DNS A/AAAA for `SITE_ADDRESS` → host; ports 80/443 reachable.
- [ ] `cp .env.example .env`; fill secrets; confirm `.env` is git-ignored (it is) and never baked into an image (`.dockerignore`).
- [ ] `sudo ./deploy/firewall.sh` (set `SSH_ALLOW_FROM`).
- [ ] Install the sshd drop-in; `sshd -t`; reload — **with a second session open**.
- [ ] `sudo ./deploy/setup-unattended-upgrades.sh`.
- [ ] `docker compose ... -f docker-compose.tls.yml config` (validate), then `up -d`.
- [ ] `./deploy/verify-nonroot.sh` passes.
- [ ] `curl -sI https://$SITE_ADDRESS` shows the cert, HSTS, and the CSP; `http://` 301s to `https://`.

## Testing / validation performed

- `docker run ... nginx:1.27-alpine nginx -t` and `docker build ./web` (validate stage runs `nginx -t`) — **PASS** with the new headers + rate-limit zones.
- `docker run ... caddy:2.8-alpine caddy validate` on `deploy/Caddyfile` — **Valid configuration** (auto HTTP→HTTPS confirmed).
- `docker compose -f docker-compose.yml -f docker-compose.tls.yml config` — **renders**; `web.ports` is empty after `!reset`.
- `bash -n deploy/*.sh` + shellcheck — clean.

## Fallback: certbot + nginx edge (not chosen)

If an org mandates certbot: add an nginx edge with `server { listen 80; location /.well-known/acme-challenge/ { root /var/www/acme; } location / { return 301 https://$host$request_uri; } }`, a `listen 443 ssl http2;` server with the modern Mozilla "intermediate" cipher suite + HSTS, a shared `acme:/var/www/acme` webroot volume, and a sidecar running `certbot renew --webroot -w /var/www/acme --deploy-hook "nginx -s reload"` on a daily timer. This reproduces what Caddy does in one process.

Sources: [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https) · [MDN CSP](https://developer.mozilla.org/docs/Web/HTTP/CSP) · [MapLibre CSP guidance](https://maplibre.org/maplibre-gl-js/docs/) · [nginx `add_header` inheritance](https://nginx.org/en/docs/http/ngx_http_headers_module.html#add_header) · [Mozilla SSL config / unattended-upgrades](https://wiki.debian.org/UnattendedUpgrades).
