#!/usr/bin/env bash
# GEO-36 — assert every service image runs as a NON-root user with the expected uid. The
# Dockerfiles all declare a dedicated user (api 10002, web 101, ingest 10001, frontend/spa
# 10003); this catches a regression where a USER line is dropped and a container silently runs
# as root. It builds any missing image, then runs `id -u` as the image's DEFAULT user (we do
# NOT pass --user, so we observe the baked-in USER) and fails if uid == 0 or != expected.
#
# Usage:  ./deploy/verify-nonroot.sh
# (The optional `caddy` TLS edge is intentionally excluded: the official Caddy image runs as
#  root to bind privileged ports 80/443 — expected for an edge terminator.)
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="${COMPOSE:-docker compose}"

# service : image : expected-uid  (image names are fixed in docker-compose.yml; uids per Dockerfile)
ROWS=(
	"api:geo-energy/api:10002"
	"web:geo-energy/web:101"
	"ingest:geo-energy/ingest:10001"
	"frontend:geo-energy/frontend:10003"
)

fail=0
for row in "${ROWS[@]}"; do
	IFS=':' read -r svc image expected <<<"$row"

	if ! docker image inspect "$image" >/dev/null 2>&1; then
		echo "… building $image (not present locally)"
		$COMPOSE --profile ingest --profile build build "$svc" >/dev/null
	fi

	# Run `id -u` as the image's default (baked-in) USER. Override only the entrypoint.
	uid="$(docker run --rm --entrypoint id "$image" -u 2>/dev/null | tr -d '[:space:]')"

	if [[ "$uid" == "0" || -z "$uid" ]]; then
		echo "FAIL  $svc ($image): runs as uid '${uid:-<none>}' (root or unknown)"
		fail=1
	elif [[ "$uid" != "$expected" ]]; then
		echo "WARN  $svc ($image): uid $uid (expected $expected) — non-root OK but unexpected"
	else
		echo "OK    $svc ($image): non-root uid $uid"
	fi
done

if [[ "$fail" -ne 0 ]]; then
	echo "verify-nonroot: FAILED — a service runs as root" >&2
	exit 1
fi
echo "verify-nonroot: all services run non-root."
