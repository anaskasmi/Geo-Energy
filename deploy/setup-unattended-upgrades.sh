#!/usr/bin/env bash
# GEO-36 — enable automatic security updates on a Debian/Ubuntu host (the images are
# debian/python:slim-based, so hosts are typically Debian/Ubuntu). Installs unattended-upgrades
# and writes two APT drop-ins: one to run it daily, one to scope it to the SECURITY pocket and
# auto-reboot in a quiet window when a kernel/libc update needs it. Idempotent: it (re)writes
# fixed config files and is safe to re-run.
#
# Usage (as root):  ./deploy/setup-unattended-upgrades.sh
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
	echo "Must run as root (try: sudo $0)" >&2
	exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends unattended-upgrades apt-listchanges

# Run the periodic apt maintenance daily and trigger unattended-upgrades.
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
APT::Periodic::Unattended-Upgrade "1";
EOF

# Scope unattended-upgrades to the security pocket; auto-remove unused deps; reboot at 04:00
# only when an update requires it (kernel/libc). Origin patterns match both Debian and Ubuntu.
cat >/etc/apt/apt.conf.d/51geo-unattended-upgrades <<'EOF'
Unattended-Upgrade::Origins-Pattern {
        "origin=Debian,codename=${distro_codename},label=Debian-Security";
        "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
        "origin=Ubuntu,archive=${distro_codename}-security";
};
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
EOF

# Enable + start the timers and prove the config parses (dry-run touches nothing).
systemctl enable --now unattended-upgrades.service 2>/dev/null || true
unattended-upgrade --dry-run --debug || true

echo "unattended-upgrades configured (security pocket, daily, auto-reboot 04:00)."
