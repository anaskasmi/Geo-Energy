#!/usr/bin/env bash
# GEO-36 — host firewall baseline (ufw / Debian-Ubuntu). Default-deny inbound, allow only the
# public web ports (80/443) and SSH, with SSH locked to an allowlist CIDR when provided and
# rate-limited otherwise. Idempotent: ufw de-duplicates identical rules, so re-running is safe.
#
# Usage (as root):
#   SSH_PORT=22 SSH_ALLOW_FROM=203.0.113.0/24 ./deploy/firewall.sh
#   ./deploy/firewall.sh                 # SSH from anywhere, but rate-limited
#
# Env:
#   SSH_PORT        SSH port to allow (default 22).
#   SSH_ALLOW_FROM  CIDR allowed to reach SSH (default: any, rate-limited via `ufw limit`).
set -euo pipefail

SSH_PORT="${SSH_PORT:-22}"
SSH_ALLOW_FROM="${SSH_ALLOW_FROM:-}"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
	echo "This script configures ufw and must run as root (try: sudo $0)" >&2
	exit 1
fi

if ! command -v ufw >/dev/null 2>&1; then
	echo "ufw not found; installing (Debian/Ubuntu)..." >&2
	apt-get update -y
	apt-get install -y --no-install-recommends ufw
fi

# Default posture: deny inbound, allow outbound (the app needs egress for ACME, the ingest
# fetchers, and the agent LLM call — never inbound except the ports below).
ufw default deny incoming
ufw default allow outgoing

# SSH — keep yourself locked out at your peril: this rule is applied BEFORE `ufw enable`.
if [[ -n "$SSH_ALLOW_FROM" ]]; then
	ufw allow from "$SSH_ALLOW_FROM" to any port "$SSH_PORT" proto tcp comment 'SSH (allowlist)'
else
	# No allowlist: rate-limit SSH to blunt brute-force (ufw limit = >6 conns/30s → drop).
	ufw limit "$SSH_PORT"/tcp comment 'SSH (rate-limited)'
fi

# Public web — HTTP (ACME HTTP-01 + 80→443 redirect) and HTTPS (TLS + HTTP/3 over UDP).
ufw allow 80/tcp comment 'HTTP (ACME + redirect)'
ufw allow 443/tcp comment 'HTTPS'
ufw allow 443/udp comment 'HTTP/3 (QUIC)'

# Enable non-interactively (no-op if already active).
ufw --force enable
ufw status verbose
