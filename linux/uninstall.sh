#!/usr/bin/env bash
# ==============================================================================
# TG Power Suite — Clean Linux Uninstaller
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "\n${BLUE}======================================================${NC}"
echo -e "${BLUE}  🗑️  Uninstalling TG Power Suite${NC}"
echo -e "${BLUE}======================================================${NC}\n"

# Stop and disable systemd service
if systemctl --user is-active --quiet tg-power-suite.service 2>/dev/null; then
    echo -e "${YELLOW}[+] Stopping systemd user service...${NC}"
    systemctl --user stop tg-power-suite.service 2>/dev/null || true
fi

if systemctl --user is-enabled --quiet tg-power-suite.service 2>/dev/null; then
    echo -e "${YELLOW}[+] Disabling systemd user service...${NC}"
    systemctl --user disable tg-power-suite.service 2>/dev/null || true
fi

# Remove systemd unit
if [ -f "$HOME/.config/systemd/user/tg-power-suite.service" ]; then
    rm -f "$HOME/.config/systemd/user/tg-power-suite.service"
    systemctl --user daemon-reload 2>/dev/null || true
    echo -e "${GREEN}[OK] Removed systemd user unit${NC}"
fi

# Remove desktop launcher
if [ -f "$HOME/.local/share/applications/tg-power-suite.desktop" ]; then
    rm -f "$HOME/.local/share/applications/tg-power-suite.desktop"
    echo -e "${GREEN}[OK] Removed desktop launcher${NC}"
fi

# Remove icon
if [ -f "$HOME/.local/share/icons/hicolor/256x256/apps/tg-power-suite.png" ]; then
    rm -f "$HOME/.local/share/icons/hicolor/256x256/apps/tg-power-suite.png"
    echo -e "${GREEN}[OK] Removed application icon${NC}"
fi

# Remove CLI wrapper
if [ -f "$HOME/.local/bin/tg-power-suite" ]; then
    rm -f "$HOME/.local/bin/tg-power-suite"
    echo -e "${GREEN}[OK] Removed CLI wrapper${NC}"
fi

# Update desktop database
update-desktop-database "$HOME/.local/share/applications/" 2>/dev/null || true

echo -e "\n${GREEN}✅ TG Power Suite uninstalled successfully.${NC}\n"
