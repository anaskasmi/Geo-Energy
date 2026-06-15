# GEO-37 — Runtime protection & observability

**Decision: enforce per-IP rate + connection limits and a payload cap in nginx (`web/nginx.conf`),
with a much stricter budget for `/api/agent` (the metered LLM path), and keep observability
lightweight — a latency-aware access log + rotated docker json-file logs + a `build.success` log
recipe.** No request-path network dependency is added; the limits are in-process nginx zones. A
richer Prometheus/Grafana/Loki stack is documented as an optional future step, not built.

This doc covers BOTH sides of the agent budget: the **nginx** edge limits here, and the **API**'s
own caps (`AGENT_TIMEOUT_S`, `AGENT_MAX_MESSAGE_CHARS`, `AGENT_MAX_CONCURRENCY` + request-timing) —
described at the end and cross-linked, since the backend (GEO-21) owns their exact defaults.

## Where the http-level directives live (the load-bearing detail)

`limit_req_zone`, `limit_conn_zone`, and `log_format` are only valid in the **`http{}`** context,
but `web/nginx.conf` is copied to `/etc/nginx/conf.d/default.conf` and contains a `server{}` block.
The stock image's main `nginx.conf` does `include /etc/nginx/conf.d/*.conf;` **inside `http{}`**
(verified: line 36, within `http{` at line 13). So directives placed at the **top of our file,
before `server{`,** land in http context. They pass `nginx -t` (and the `web/Dockerfile` validate
stage). No second mounted file or Dockerfile change is needed.

```nginx
# http context (top of web/nginx.conf, before `server {`)
limit_req_zone  $binary_remote_addr zone=api_rl:10m   rate=10r/s;
limit_req_zone  $binary_remote_addr zone=agent_rl:10m rate=1r/s;
limit_conn_zone $binary_remote_addr zone=api_conns:10m;
limit_conn_zone $binary_remote_addr zone=agent_conns:10m;
log_format timed '... $status ... rt=$request_time urt=$upstream_response_time method=$request_method uri=$uri ...';
```

## Rate-limit design

| Path | `limit_req` zone | rate | burst | `limit_conn` | `client_max_body_size` |
|---|---|---|---|---|---|
| `/api/` (score, explain, context) | `api_rl` | 10 r/s | 20 (nodelay) | `api_conns` = 20 | **1m** |
| `/api/agent` (SSE, LLM) | `agent_rl` | **1 r/s** | **3 (nodelay)** | `agent_conns` = **2** | **256k** |

- **Why stricter on `/api/agent`:** every request spends the paid Gemini key (GEO-21). 1 r/s with
  burst 3 absorbs a few rapid sends; `agent_conns 2` caps simultaneous long-lived SSE streams per
  IP so one client can't pin open many generations. `agent_conns` is a **separate** zone from
  `api_conns` so normal API traffic doesn't starve, and vice-versa.
- **`nodelay`** lets a legitimate burst through immediately, then returns **429** (`limit_req_status
  429` / `limit_conn_status 429`) instead of queueing — cleaner for clients/CDNs than the default 503.
- **Payload cap:** drawn polygons are bounded; `1m` on `/api/` is generous headroom over a complex
  multi-ring AOI while capping abusive bodies. `/api/agent` is text chat → `256k` (paired with the
  API's `AGENT_MAX_MESSAGE_CHARS`).

### Real-client-IP behind an edge/CDN

The zones key on `$binary_remote_addr`. Behind the Caddy edge (GEO-36) or a CDN, that would be the
edge's IP — every client sharing one bucket. `web/nginx.conf` therefore trusts `X-Forwarded-For`
**only from RFC1918 private sources** (`set_real_ip_from 10/8, 172.16/12, 192.168/16` +
`real_ip_recursive on`): the edge sits on the private compose network, so its `X-Forwarded-For` is
honoured, while a directly-connected public client cannot spoof it. In production `web` is not
published directly (the TLS override `!reset`s its ports) — so the edge is the only hop and this is
safe. **Caveat:** if you *do* expose `:8080` directly to the internet, do not also place a private-IP
proxy in front without re-checking this trust list.

## SSE correctness for `/api/agent`

`location /api/agent` (a longer prefix than `/api/`, so nginx prefers it) sets `proxy_buffering off`,
`proxy_cache off`, `gzip off`, `chunked_transfer_encoding on`, `proxy_http_version 1.1` +
`Connection ""` (keep-alive upstream), and `proxy_read_timeout/proxy_send_timeout 300s`. The app
emits `X-Accel-Buffering: no`; we **never** `proxy_hide_header` it, and `proxy_buffering off` already
guarantees tokens flush as they arrive. At the Caddy edge the matching `@agent` route sets
`flush_interval -1` so the edge doesn't re-buffer the stream.

## Observability

**Latency in the access log** — `log_format timed` records `$status`, `$request_time`,
`$upstream_response_time`, `$request_method`, `$uri`. Watch p95/upstream latency with:

```bash
docker compose logs web | grep -oE 'rt=[0-9.]+ urt=[0-9.-]+' | sort -t= -k2 -n | tail
```

**Log rotation** — both long-running services (`api`, `web`) get a json-file driver with
`max-size: 10m` / `max-file: 3` in `docker-compose.yml`, bounding disk use on a small VPS (the
default json-file log is unbounded). One-shot `ingest`/`frontend` are excluded.

**Ingestion build success** — the ingest container emits structured JSON with terminal
`build.success` / `build.failed` events (carrying `build_id`). `deploy/check-build.sh` surfaces them:

```bash
docker compose logs ingest | jq 'select(.event=="build.success" or .event=="build.failed")'
./deploy/check-build.sh --last     # prints the latest; exit 0 on success, 1 on failure/none
```

**Optional richer stack (future, not built):** a Prometheus `nginx-prometheus-exporter` + node-exporter
+ Grafana, or Loki + Promtail to ship the json-file logs. Deferred — the access log + `jq` recipes
cover the operational need at this scale without another always-on service.

## API side (GEO-21, owned by the backend) — cross-link

The nginx limits are the outer guard; the API enforces the inner ones. `docker-compose.yml` passes
these to `api` via `${VAR:-default}` (real values from `.env`), and `.env.example` documents them
(the **API owns the authoritative defaults** — values below are the placeholders kept in sync here):

| Env var | Purpose | nginx counterpart |
|---|---|---|
| `AGENT_TIMEOUT_S` (≈300) | Server-side abort for one agent run — google-genai streaming has no clean transport-close handle, so a hard timeout is required. | `proxy_read_timeout 300s` |
| `AGENT_MAX_MESSAGE_CHARS` (≈8000) | Reject oversized chat messages before the LLM. | `client_max_body_size 256k` on `/api/agent` |
| `AGENT_MAX_CONCURRENCY` (≈4) | Cap in-flight agent runs process-wide (protects the key + the 1g/2cpu container). | `limit_conn agent_conns 2` (per-IP) |

The API also has a **request-timing** path (`api/app/perf.py`, the ETag middleware + `perf` timing
helpers) and the GEO-19 `<2s` scoring budget — the `timed` access log gives the matching outside-in
latency view. These are described at a high level; see the GEO-21 agent work for exact defaults so
the orchestrator can reconcile the placeholder values above.

## Testing / validation performed

- `docker build ./web` (validate stage `nginx -t`) — **PASS** with the zones in http context + the per-location limits.
- `docker run ... nginx:1.27-alpine nginx -t` on the mounted config — **syntax ok / test successful**.
- `docker compose config` renders the json-file `logging:` on api + web and the agent env passthrough.
- `bash -n deploy/check-build.sh` + shellcheck — clean.

Sources: [nginx `limit_req`](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html) · [`limit_conn`](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html) · [`realip` module](https://nginx.org/en/docs/http/ngx_http_realip_module.html) · [docker json-file logging](https://docs.docker.com/config/containers/logging/json-file/) · [nginx SSE/buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering).
