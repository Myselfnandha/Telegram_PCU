#!/usr/bin/env bash
# ==============================================================================
# TG Power Suite — Headless Background Launcher with Desktop Notifications
#
# Launches the application manually in the background without opening any GUI or
# browser window, displaying a lightweight native desktop notification.
#
# Supported Commands:
#   ./launch_headless.sh          (starts headless server and notifies)
#   ./launch_headless.sh start    (starts headless server and notifies)
#   ./launch_headless.sh stop     (stops running server and notifies)
#   ./launch_headless.sh restart  (restarts headless server and notifies)
#   ./launch_headless.sh toggle   (toggles running state and notifies)
#   ./launch_headless.sh status   (checks health and displays notification)
#   ./launch_headless.sh open     (opens Web UI dashboard in default browser)
# ==============================================================================

set -eo pipefail

# Resolve canonical path across symlinks
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
if [ -f "$SCRIPT_DIR/backend/run.py" ]; then
    APP_DIR="$(cd "$SCRIPT_DIR" && pwd)"
else
    APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
VENV_PYTHON="$APP_DIR/venv/bin/python"
BACKEND_RUN="$APP_DIR/backend/run.py"
PID_FILE="$APP_DIR/backend/data/tg_power_suite.pid"
LOG_FILE="$APP_DIR/backend/data/tg_power_suite.log"
ICON_PATH="$APP_DIR/assets/tg-power-suite.png"
HOST="127.0.0.1"
PORT="8088"
HEALTH_URL="http://${HOST}:${PORT}/api/system/health"

# Fall back to alternative icon if primary is absent
if [ ! -f "$ICON_PATH" ]; then
    ICON_PATH="$APP_DIR/frontend/assets/icon-192.png"
fi

# ------------------------------------------------------------------------------
# Notification Dispatcher
# Sanitizes LD_LIBRARY_PATH to avoid AppImage library clashes and falls back to D-Bus
# ------------------------------------------------------------------------------
send_notification() {
    local title="$1"
    local message="$2"
    local urgency="${3:-normal}"
    local expire_ms="${4:-4000}"

    # Attempt standard notify-send with clean environment
    if env -u LD_LIBRARY_PATH notify-send \
        -a "TG Power Suite" \
        -u "$urgency" \
        -t "$expire_ms" \
        ${ICON_PATH:+-i "$ICON_PATH"} \
        "$title" "$message" 2>/dev/null; then
        return 0
    fi

    # Fallback directly to Freedesktop D-Bus interface
    local dbus_urgency=1
    if [ "$urgency" = "critical" ]; then
        dbus_urgency=2
    elif [ "$urgency" = "low" ]; then
        dbus_urgency=0
    fi

    gdbus call --session \
        --dest org.freedesktop.Notifications \
        --object-path /org/freedesktop/Notifications \
        --method org.freedesktop.Notifications.Notify \
        "TG Power Suite" 0 "${ICON_PATH:-dialog-information}" "$title" "$message" '[]' "{\"urgency\": <${dbus_urgency}>}" "$expire_ms" >/dev/null 2>&1 || true
}

# ------------------------------------------------------------------------------
# Process Discovery & Health Verification
# ------------------------------------------------------------------------------
get_running_pid() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE" 2>/dev/null | tr -d '[:space:]')
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
    fi

    local proc_pid
    proc_pid=$(pgrep -f "backend/run.py" 2>/dev/null | head -n 1 || true)
    if [ -n "$proc_pid" ] && kill -0 "$proc_pid" 2>/dev/null; then
        echo "$proc_pid"
        return 0
    fi

    if command -v systemctl >/dev/null 2>&1; then
        local sys_pid
        sys_pid=$(systemctl --user show --property MainPID --value tg-power-suite.service 2>/dev/null || true)
        if [ -n "$sys_pid" ] && [ "$sys_pid" -gt 0 ] 2>/dev/null && kill -0 "$sys_pid" 2>/dev/null; then
            echo "$sys_pid"
            return 0
        fi
    fi

    return 1
}

is_healthy() {
    curl -s -f -m 1 "$HEALTH_URL" >/dev/null 2>&1
}

do_start() {
    local current_pid
    current_pid=$(get_running_pid || true)

    if [ -n "$current_pid" ]; then
        echo "✅ TG Power Suite is already running (PID: $current_pid)"
        send_notification \
            "TG Power Suite" \
            "Application is already running in background (PID: $current_pid)\nDashboard: http://localhost:$PORT" \
            "normal"
        return 0
    fi

    echo "🚀 Starting TG Power Suite in background (Headless)..."

    "$VENV_PYTHON" "$BACKEND_RUN" start --daemon

    local ready=0
    for _ in {1..10}; do
        sleep 0.5
        current_pid=$(get_running_pid || true)
        if [ -n "$current_pid" ] && is_healthy; then
            ready=1
            break
        fi
    done

    if [ "$ready" -eq 1 ]; then
        echo "✅ Started successfully (PID: $current_pid) — listening on http://localhost:$PORT"
        send_notification \
            "TG Power Suite" \
            "Server started in background (Headless)\nWeb Dashboard: http://localhost:$PORT (PID: $current_pid)" \
            "normal"
        return 0
    else
        current_pid=$(get_running_pid || true)
        if [ -n "$current_pid" ]; then
            echo "⚠️ Server started with PID $current_pid, still warming up."
            send_notification \
                "TG Power Suite" \
                "Server started (PID: $current_pid), warming up...\nWeb Dashboard: http://localhost:$PORT" \
                "normal"
            return 0
        else
            echo "❌ Failed to start TG Power Suite. Check logs: $LOG_FILE"
            send_notification \
                "TG Power Suite" \
                "Failed to start headless server. Check backend logs." \
                "critical"
            return 1
        fi
    fi
}

do_stop() {
    local current_pid
    current_pid=$(get_running_pid || true)

    echo "🛑 Stopping TG Power Suite..."

    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user stop tg-power-suite.service tg-rust-core.service 2>/dev/null || true
    fi

    if [ -f "$VENV_PYTHON" ] && [ -f "$BACKEND_RUN" ]; then
        "$VENV_PYTHON" "$BACKEND_RUN" stop 2>/dev/null || true
    fi

    if [ -n "$current_pid" ] && kill -0 "$current_pid" 2>/dev/null; then
        kill -15 "$current_pid" 2>/dev/null || true
        sleep 0.5
        kill -9 "$current_pid" 2>/dev/null || true
    fi

    pkill -f "tg-rust-core" 2>/dev/null || true
    pkill -f "backend/run.py" 2>/dev/null || true
    pkill -f "scripts/tray_app.py" 2>/dev/null || true

    rm -f "$PID_FILE"

    sleep 0.5
    local remaining
    remaining=$(pgrep -f "tg-rust-core|backend/run.py" 2>/dev/null || true)
    if [ -n "$remaining" ]; then
        echo "$remaining" | xargs -r kill -9 2>/dev/null || true
    fi

    echo "✅ TG Power Suite stopped."
    send_notification \
        "TG Power Suite" \
        "Background server stopped." \
        "normal"
}

do_restart() {
    echo "🔄 Restarting TG Power Suite..."
    do_stop
    sleep 1
    do_start
}

do_status() {
    local current_pid
    current_pid=$(get_running_pid || true)

    if [ -n "$current_pid" ]; then
        local health_status="Offline probe"
        if is_healthy; then
            health_status="Healthy & Responsive"
        fi
        echo "✅ TG Power Suite is running (PID: $current_pid) — $health_status"
        echo "   • Dashboard: http://localhost:$PORT"
        echo "   • Log File:  $LOG_FILE"
        send_notification \
            "TG Power Suite Status" \
            "Status: Running (PID: $current_pid)\nState: $health_status\nDashboard: http://localhost:$PORT" \
            "normal"
    else
        echo "⭕ TG Power Suite is stopped."
        send_notification \
            "TG Power Suite Status" \
            "Status: Stopped (Not Running)" \
            "low"
    fi
}

do_toggle() {
    local current_pid
    current_pid=$(get_running_pid || true)

    if [ -n "$current_pid" ]; then
        do_stop
    else
        do_start
    fi
}

do_open() {
    echo "🌐 Opening Web Dashboard at http://localhost:$PORT..."
    if ! get_running_pid >/dev/null 2>&1; then
        do_start
    fi
    xdg-open "http://localhost:$PORT" 2>/dev/null || true
}

# ------------------------------------------------------------------------------
# Command Dispatch
# ------------------------------------------------------------------------------
case "${1:-start}" in
    start)
        do_start
        ;;
    stop)
        do_stop
        ;;
    restart)
        do_restart
        ;;
    status)
        do_status
        ;;
    toggle)
        do_toggle
        ;;
    open)
        do_open
        ;;
    -h|--help|help)
        echo "Usage: $(basename "$0") [start|stop|restart|status|toggle|open]"
        echo ""
        echo "Commands:"
        echo "  start    Start server manually in background without GUI (default)"
        echo "  stop     Stop running server"
        echo "  restart  Restart background server"
        echo "  status   Check health status and send notification"
        echo "  toggle   Toggle running state (start if stopped, stop if running)"
        echo "  open     Ensure server is running and open browser dashboard"
        ;;
    *)
        echo "Unknown command: $1"
        echo "Usage: $(basename "$0") [start|stop|restart|status|toggle|open]"
        exit 1
        ;;
esac
