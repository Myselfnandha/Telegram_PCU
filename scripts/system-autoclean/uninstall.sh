#!/usr/bin/env bash
# Uninstaller for system-autoclean service and timer

set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Error: This uninstaller must be run with root privileges (sudo)." >&2
    echo "Usage: sudo bash $0" >&2
    exit 1
fi

echo "==> Stopping and disabling system-autoclean.timer..."
systemctl disable --now system-autoclean.timer 2>/dev/null || true
systemctl stop system-autoclean.service 2>/dev/null || true

echo "==> Removing systemd unit files..."
rm -f /etc/systemd/system/system-autoclean.service
rm -f /etc/systemd/system/system-autoclean.timer

echo "==> Removing executable from /usr/local/bin..."
rm -f /usr/local/bin/system-autoclean

echo "==> Removing lock file if present..."
rm -f /run/lock/system-autoclean.lock

echo "==> Reloading systemd daemon..."
systemctl daemon-reload

echo "Uninstallation complete. All system-autoclean components removed."
