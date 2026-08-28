#!/usr/bin/env bash
# ==============================================================================
# TG Power Suite — Smart Linux Installer & Auto-Updater
# Checks if installed -> Updates -> Falls back to clean reinstall if update fails -> Else installs
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$APP_DIR/venv"
SERVICE_NAME="tg-power-suite.service"
DESKTOP_NAME="tg-power-suite.desktop"
CLI_NAME="tg-power-suite"

SYSTEMD_DIR="$HOME/.config/systemd/user"
APPLICATIONS_DIR="$HOME/.local/share/applications"
ICONS_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
BIN_DIR="$HOME/.local/bin"

echo -e "\n${BLUE}======================================================================${NC}"
echo -e "${BLUE}  🚀 TG Power Suite — Smart Linux Installer & Auto-Updater${NC}"
echo -e "${BLUE}======================================================================${NC}\n"

# Function to check if TG Power Suite is installed
is_installed() {
    if [ -f "$SYSTEMD_DIR/$SERVICE_NAME" ] || [ -f "$APPLICATIONS_DIR/$DESKTOP_NAME" ] || [ -f "$BIN_DIR/$CLI_NAME" ]; then
        return 0
    fi
    return 1
}

# Function to cleanly uninstall
run_uninstall() {
    echo -e "${YELLOW}[!] Running clean uninstallation routine...${NC}"
    chmod +x "$SCRIPT_DIR/uninstall.sh" 2>/dev/null || true
    "$SCRIPT_DIR/uninstall.sh" || true
}

# Function to perform clean installation
do_fresh_install() {
    echo -e "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}  📦 Performing Fresh Installation of TG Power Suite${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

    mkdir -p "$SYSTEMD_DIR" "$APPLICATIONS_DIR" "$ICONS_DIR" "$BIN_DIR"

    # 1. Virtual Environment Setup
    if [ ! -d "$VENV_DIR" ]; then
        echo -e "${YELLOW}[+] Creating Python virtual environment...${NC}"
        python3 -m venv --system-site-packages "$VENV_DIR"
    fi

    echo -e "${YELLOW}[+] Installing / Verifying Python dependencies...${NC}"
    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install -r "$APP_DIR/backend/requirements.txt"

    # 2. Copy Icons
    echo -e "${YELLOW}[+] Installing application icons...${NC}"
    if [ -f "$APP_DIR/assets/tg-power-suite.png" ]; then
        cp "$APP_DIR/assets/tg-power-suite.png" "$ICONS_DIR/tg-power-suite.png"
    elif [ -f "$APP_DIR/assets/tg-fdm-proxy.png" ]; then
        cp "$APP_DIR/assets/tg-fdm-proxy.png" "$ICONS_DIR/tg-power-suite.png"
    fi

    # 3. Create CLI wrapper in ~/.local/bin
    echo -e "${YELLOW}[+] Installing CLI wrapper: $BIN_DIR/$CLI_NAME...${NC}"
    cat <<EOF > "$BIN_DIR/$CLI_NAME"
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" "$APP_DIR/backend/run.py" "\$@"
EOF
    chmod +x "$BIN_DIR/$CLI_NAME"

    # 4. Install Desktop Launcher
    echo -e "${YELLOW}[+] Installing Desktop Launcher...${NC}"
    cat <<EOF > "$APPLICATIONS_DIR/$DESKTOP_NAME"
[Desktop Entry]
Name=TG Power Suite
Comment=Turbo MTProto Uploader & High-Speed FDM Proxy for Telegram
Exec=$BIN_DIR/$CLI_NAME
Icon=tg-power-suite
Terminal=false
Type=Application
Categories=Network;FileTransfer;Utility;
Keywords=Telegram;Uploader;FDM;Proxy;Downloader;MTProto;
StartupNotify=true
Actions=OpenDashboard;OpenSettings;

[Desktop Action OpenDashboard]
Name=Open Web Dashboard
Exec=/usr/bin/env xdg-open http://localhost:8088

[Desktop Action OpenSettings]
Name=Settings
Exec=/usr/bin/env xdg-open http://localhost:8088#settings
EOF
    chmod +x "$APPLICATIONS_DIR/$DESKTOP_NAME"
    update-desktop-database "$APPLICATIONS_DIR" 2>/dev/null || true

    # 5. Install and Enable Systemd User Service
    echo -e "${YELLOW}[+] Setting up systemd user service ($SERVICE_NAME)...${NC}"
    cat <<EOF > "$SYSTEMD_DIR/$SERVICE_NAME"
[Unit]
Description=TG Power Suite (Telegram MTProto Turbo Uploader & FDM Proxy)
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/python $APP_DIR/backend/run.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICE_NAME"

    echo -e "\n${GREEN}======================================================================${NC}"
    echo -e "${GREEN}  🎉 TG Power Suite Successfully Installed & Running!${NC}"
    echo -e "${GREEN}======================================================================${NC}"
    echo -e "  • Web Dashboard   : ${CYAN}http://localhost:8088${NC}"
    echo -e "  • CLI Command     : ${CYAN}tg-power-suite [start|stop|status|logs]${NC}"
    echo -e "  • Service Status  : ${CYAN}systemctl --user status tg-power-suite.service${NC}"
    echo -e "${GREEN}======================================================================${NC}\n"
}

# Function to perform safe in-place update
do_update() {
    echo -e "${CYAN}[!] Existing TG Power Suite installation detected.${NC}"
    echo -e "${CYAN}[+] Attempting safe in-place update...${NC}\n"

    # Step 1: Stop service temporarily
    if systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        echo -e "${YELLOW}[1/5] Stopping systemd user service...${NC}"
        systemctl --user stop "$SERVICE_NAME" || return 1
    fi

    # Step 2: Update dependencies
    echo -e "${YELLOW}[2/5] Updating Python virtual environment & packages...${NC}"
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv --system-site-packages "$VENV_DIR" || return 1
    fi
    "$VENV_DIR/bin/pip" install --upgrade pip || return 1
    "$VENV_DIR/bin/pip" install -r "$APP_DIR/backend/requirements.txt" || return 1

    # Step 3: Update desktop & CLI entries
    echo -e "${YELLOW}[3/5] Updating Desktop launcher and icons...${NC}"
    mkdir -p "$APPLICATIONS_DIR" "$ICONS_DIR" "$BIN_DIR"
    if [ -f "$APP_DIR/assets/tg-power-suite.png" ]; then
        cp "$APP_DIR/assets/tg-power-suite.png" "$ICONS_DIR/tg-power-suite.png"
    fi

    cat <<EOF > "$BIN_DIR/$CLI_NAME"
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" "$APP_DIR/backend/run.py" "\$@"
EOF
    chmod +x "$BIN_DIR/$CLI_NAME"

    cat <<EOF > "$APPLICATIONS_DIR/$DESKTOP_NAME"
[Desktop Entry]
Name=TG Power Suite
Comment=Turbo MTProto Uploader & High-Speed FDM Proxy for Telegram
Exec=$BIN_DIR/$CLI_NAME
Icon=tg-power-suite
Terminal=false
Type=Application
Categories=Network;FileTransfer;Utility;
Keywords=Telegram;Uploader;FDM;Proxy;Downloader;MTProto;
StartupNotify=true
Actions=OpenDashboard;OpenSettings;

[Desktop Action OpenDashboard]
Name=Open Web Dashboard
Exec=/usr/bin/env xdg-open http://localhost:8088

[Desktop Action OpenSettings]
Name=Settings
Exec=/usr/bin/env xdg-open http://localhost:8088#settings
EOF
    chmod +x "$APPLICATIONS_DIR/$DESKTOP_NAME"
    update-desktop-database "$APPLICATIONS_DIR" 2>/dev/null || true

    # Step 4: Update Systemd Service Unit
    echo -e "${YELLOW}[4/5] Updating systemd user service unit...${NC}"
    mkdir -p "$SYSTEMD_DIR"
    cat <<EOF > "$SYSTEMD_DIR/$SERVICE_NAME"
[Unit]
Description=TG Power Suite (Telegram MTProto Turbo Uploader & FDM Proxy)
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/python $APP_DIR/backend/run.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    
    # Step 5: Restart Service
    echo -e "${YELLOW}[5/5] Restarting background service...${NC}"
    systemctl --user restart "$SERVICE_NAME" || return 1

    echo -e "\n${GREEN}======================================================================${NC}"
    echo -e "${GREEN}  ✨ TG Power Suite Successfully Updated & Running!${NC}"
    echo -e "${GREEN}======================================================================${NC}"
    echo -e "  • Web Dashboard   : ${CYAN}http://localhost:8088${NC}"
    echo -e "  • Service Status  : ${CYAN}systemctl --user status tg-power-suite.service${NC}"
    echo -e "${GREEN}======================================================================${NC}\n"
    return 0
}

# Main Execution Flow
if is_installed; then
    if do_update; then
        exit 0
    else
        echo -e "\n${RED}[!] Update encountered an error.${NC}"
        echo -e "${YELLOW}[+] Falling back to clean uninstall and fresh reinstall...${NC}\n"
        run_uninstall
        do_fresh_install
    fi
else
    do_fresh_install
fi
