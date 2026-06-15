# GEO-38 — CI/CD pipeline + optional CDN

**Decision: two GitHub Actions workflows — an ungated `CI` (test + build-proof on every push/PR) and
a gated `Deploy` (build+push to GHCR, then SSH-roll the host), with every secret in GitHub Actions /
a `production` Environment and nothing hardcoded. A CDN is recommended for the static SPA and
`.pmtiles` only (CloudFront or Cloudflare); the API and especially the `/api/agent` SSE stream are
NEVER fronted by a CDN.**

## CI — `.github/workflows/ci.yml` (push/PR → main)

| Job | Runner | Steps | Gates |
|---|---|---|---|
| `api-tests` | ubuntu, py 3.12 | `pip install -r api/requirements-dev.txt`; `cd api && pytest -q` | — |
| `frontend-build` | ubuntu, node 22 | `npm ci`; `npm run build` (`tsc -b && vite build`) | — |
| `docker-build` | ubuntu + buildx | `docker compose --profile ingest --profile build build` (all 4 images) | **needs** `api-tests`, `frontend-build` |

No secrets: the agent/key vars are runtime-only and `MAPBOX_TOKEN` is an empty public build arg
here. `concurrency` cancels superseded runs; `permissions: contents: read` is least-privilege.
`docker-build` only proves the Dockerfiles build — it never pushes.

## Deploy — `.github/workflows/deploy.yml` (gated)

```
push:main / workflow_dispatch
        │
        ▼
 build-and-push  (matrix: api, web, ingest, frontend)
   • login GHCR with the ephemeral GITHUB_TOKEN (packages: write)
   • build-push-action → ghcr.io/<owner>/<repo>/<svc>:<sha> + :latest  (gha build cache)
        │
        ▼
 deploy   environment: production   if: ref == refs/heads/main
   • GATE: required reviewers / branch rules on the `production` environment
   • SSH (key from secrets) → host: docker compose pull api web && up -d && image prune
```

What's gated / where the secrets live:

| Secret (scope) | Used for |
|---|---|
| `GITHUB_TOKEN` (auto, job `packages: write`) | push images to GHCR — no PAT needed |
| `DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_SSH_KEY` / `DEPLOY_PATH` (**`production` environment**) | SSH roll-out; the deploy step **hard-fails** if any is unset |
| `MAPBOX_TOKEN` (repo/env, optional) | public Vite build arg for the frontend image |

The `deploy` job runs only on `main` and only after the `production` environment gate (configure
required reviewers there). The SSH step is a transparent **scaffold** — it writes the key to a
0600 file and runs `docker compose pull/up` in `DEPLOY_PATH` on the host. The host checkout holds
`docker-compose.yml` plus a small override pinning the GHCR images to `${IMAGE_TAG}`, e.g.:

```yaml
# docker-compose.deploy.yml (lives on the host; not in this repo's request path)
services:
  api: { image: "ghcr.io/<owner>/<repo>/api:${IMAGE_TAG:-latest}" }
  web: { image: "ghcr.io/<owner>/<repo>/web:${IMAGE_TAG:-latest}" }
```

> GHCR image paths must be **lowercase**; keep the repo name lowercase or down-case `IMAGE_NAMESPACE`.

## Local convenience

`make ci` mirrors CI locally (API tests in the ingest/api venv path + frontend build + `compose
build`). See the README "Deployment & CI/CD" section.

## Optional CDN [RESEARCH]

**Recommendation: front only the static, cache-friendly origins; keep the dynamic API same-origin
and uncached.**

| Origin path | CDN it? | Why / cache key |
|---|---|---|
| `/assets/*` (hashed JS/CSS) | **Yes** | Content-addressed; already `Cache-Control: public, max-age=31536000, immutable`. Ideal CDN object. |
| `/index.html` | Edge-cache with short TTL or bypass | Already `no-cache` so a redeploy is seen immediately; let the CDN revalidate. |
| `/data/*.pmtiles` | **Yes (carefully)** | nginx already serves `Accept-Ranges: bytes` + permissive CORS + immutable cache — the CDN must **honour byte-range/`Range`** requests (CloudFront & Cloudflare do) or MapLibre's pmtiles range reads break. |
| `/api/*` | **No** | Dynamic, per-request, auth/rate-limited; caching would serve stale scores. |
| `/api/agent` | **Never** | SSE stream; CDNs buffer/scan responses and kill `text/event-stream`. |

The existing headers already make this work: immutable `max-age` on `/assets` + pmtiles, `no-cache`
on `index.html`, and CORS/`Accept-Ranges` on pmtiles. **Same-origin caveat:** if `/api` and the SPA
share an origin (they do — both behind the edge), routing static paths through a CDN while `/api`
hits the origin keeps the API same-origin, so no CORS changes are needed. If instead the CDN fronts
*everything* and forwards `/api` to the origin, ensure it forwards the `Host`/`X-Forwarded-For` and
does **not** cache `/api*`.

**CloudFront vs Cloudflare:** CloudFront pairs naturally if hosting on AWS (S3/origin behaviors per
path-pattern, range support, fine-grained cache policies); Cloudflare is simplest as a DNS-level
proxy (orange-cloud, "Cache Everything" page rule scoped to `/assets/*` + `*.pmtiles`, bypass
`/api/*`). Either way: **doc-level only — no required infra.** Trade-off: a CDN cuts origin
bandwidth/latency for the (large, immutable) tiles + bundle, at the cost of one more cache layer to
reason about for `index.html` freshness and pmtiles range correctness.

## Testing / validation performed

- `yaml.safe_load` (pyyaml 6.0.3) on both workflows — **ok**.
- `actionlint` (rhysd/actionlint:latest via docker) — **clean, exit 0**.
- `docker compose --profile ingest --profile build build` — all four images build locally
  (`deploy/verify-nonroot.sh` builds + confirms non-root uids 10002/101/10001/10003).

Sources: [GitHub Actions environments + required reviewers](https://docs.github.com/actions/deployment/targeting-different-environments/using-environments-for-deployment) · [Publishing to GHCR with `GITHUB_TOKEN`](https://docs.github.com/packages/managing-github-packages-using-github-actions-workflows/publishing-and-installing-a-package-with-github-actions) · [`docker/build-push-action`](https://github.com/docker/build-push-action) · [CloudFront range/cache behaviors](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/RangeGETs.html) · [Cloudflare cache rules](https://developers.cloudflare.com/cache/).
