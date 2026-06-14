# PLACEHOLDER SKELETON (GEO-1).
# Production nginx config (SPA history fallback, /api proxy_pass, *.pmtiles Range/CORS +
# immutable cache, gzip/brotli) is GEO-34; the hardened multi-stage image is GEO-33.
# This stub only makes the `web` service buildable/bootable.
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf

# SPA is mounted at /usr/share/nginx/html (web_dist volume); *.pmtiles served from /data.
EXPOSE 80
