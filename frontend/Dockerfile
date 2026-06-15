# Frontend (SPA) — production build image (GEO-33): multi-stage, non-root. This is a
# build-only one-shot: it compiles the React+Vite+MapLibre app and copies the static dist/
# into the shared web_dist volume, which the `web` nginx service serves. The real app is
# GEO-22/26/23.

# ---- builder: install pinned deps from the lockfile + vite build with build-time env ----
FROM node:22-alpine AS builder
WORKDIR /app

# Build-time public config baked into the client bundle (Vite inlines VITE_* at build).
# These are PUBLIC by design (a Mapbox token is a publishable client token); real server
# secrets (ANTHROPIC_API_KEY, NREL_API_KEY) NEVER reach the frontend build.
ARG VITE_API_BASE_URL=/api
ARG VITE_MAPBOX_TOKEN=
ARG VITE_PARCELS_PMTILES_URL=/data/current/parcels.pmtiles
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_MAPBOX_TOKEN=$VITE_MAPBOX_TOKEN \
    VITE_PARCELS_PMTILES_URL=$VITE_PARCELS_PMTILES_URL

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# ---- export: minimal non-root stage that copies dist/ into the web_dist volume (/out) ----
FROM alpine:3.20 AS export
RUN addgroup -S spa && adduser -S -G spa -u 10003 spa
COPY --from=builder /app/dist /opt/dist
# Own /out in the image so a fresh web_dist volume inherits writable (non-root) ownership on
# first mount; dist files stay world-readable so nginx (a different uid) can serve them.
RUN mkdir -p /out && chown -R spa:spa /out /opt/dist
USER spa
CMD ["sh", "-c", "cp -a /opt/dist/. /out/"]
