# Web (nginx) — production image (GEO-33): multi-stage (validate config → unprivileged
# runtime), non-root, HEALTHCHECK. Serves the SPA + proxies /api + serves *.pmtiles (GEO-34).

# ---- validate: fail the build on an invalid nginx config ----
FROM nginx:1.27-alpine AS validate
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN nginx -t

# ---- runtime: unprivileged (non-root, uid 101) nginx listening on :8080 ----
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY --from=validate /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

# Liveness: /healthz returns 200 from nginx itself (independent of SPA/artifact state).
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
