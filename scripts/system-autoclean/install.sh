#!/usr/bin/env bash
# Installer for system-autoclean service and timer

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Error: This installer must be run with root privileges (sudo)." >&2
    echo "Usage: sudo bash $0" >&2
    exit 1
fi

echo "==> Installing system-autoclean executable to /usr/local/bin..."
install -m 0755 "$SCRIPT_DIR/system-autoclean.sh" /usr/local/bin/system-autoclean

echo "==> Installing systemd units to /etc/systemd/system..."
install -m 0644 "$SCRIPT_DIR/system-autoclean.service" /etc/systemd/system/system-autoclean.service
install -m 0644 "$SCRIPT_DIR/system-autoclean.timer" /etc/systemd/system/system-autoclean.timer

echo "==> Reloading systemd daemon..."
systemctl daemon-reload

echo "==> Enabling and starting system-autoclean.timer..."
systemctl enable --now system-autoclean.timer

echo ""
echo "==> Verification: Active timer schedule:"
systemctl list-timers --no-pager system-autoclean.timer

echo ""
echo "Installation successful."
echo "Commands:"
echo "  Run manually:       sudo system-autoclean"
echo "  Dry-run preview:    system-autoclean --dry-run"
echo "  Check timer status: system-autoclean --status"
echo "  Uninstall:          sudo bash $SCRIPT_DIR/uninstall.sh"
