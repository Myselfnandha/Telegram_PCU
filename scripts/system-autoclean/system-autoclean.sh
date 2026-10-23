#!/usr/bin/env bash
# system-autoclean: Comprehensive automated cleanup daemon and CLI.
# Target Domains:
#   1. Flatpak (unused runtimes, ostree repair, staging caches)
#   2. Pacman (retaining last 1 package version, purging uninstalled)
#   3. Docker (stopped containers, dangling images, build cache; preserves named volumes)
#   4. Systemd (journalctl vacuum, coredump pruning, tmpfiles, stale /var/tmp)
#   5. User Caches (Yay AUR, Trash >14d, Thumbnails >14d, Pip/Uv/Go dev caches)
# Interface: system-autoclean [--dry-run|-n] [--status|-s] [--help|-h]

set -euo pipefail

LOCK_FILE="/run/lock/system-autoclean.lock"
DRY_RUN=0
STATUS_ONLY=0
TRASH_DAYS=14
COREDUMP_DAYS=14
THUMBNAIL_DAYS=14

usage() {
    cat <<EOF
Usage: system-autoclean [OPTIONS]

Comprehensive system & user cache cleanup utility.

Clean Targets:
  • Flatpak unused runtimes, OSTree repository pruning, staging caches
  • Pacman package cache (keeps 1 version for rollback) & uninstalled packages
  • Docker dangling images, stopped containers, build cache (preserves named volumes)
  • Systemd journal vacuum (100M/14d) and system coredumps older than 14 days
  • User Trash (>14d), desktop thumbnails (>14d), and Yay AUR build caches
  • Developer caches: pip, uv, and go build caches

Options:
  -n, --dry-run    Simulate cleanup operations and report estimated reclaimable space.
  -s, --status     Display sizes across all cache domains and systemd timer status.
  -h, --help       Show this help message.

EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -n|--dry-run)
                DRY_RUN=1
                shift
                ;;
            -s|--status)
                STATUS_ONLY=1
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1" >&2
                usage
                exit 1
                ;;
        esac
    done
}

get_free_kb() {
    df -k / | awk 'NR==2 {print $4}'
}

format_kb() {
    local kb=$1
    if (( kb >= 1048576 )); then
        awk -v kb="$kb" 'BEGIN {printf "%.2f GiB", kb / 1048576}'
    elif (( kb >= 1024 )); then
        awk -v kb="$kb" 'BEGIN {printf "%.2f MiB", kb / 1024}'
    else
        echo "${kb} KiB"
    fi
}

get_human_users() {
    awk -F: '$3 >= 1000 && $3 < 65534 && $6 ~ /^\/home/ {print $1 ":" $3 ":" $6}' /etc/passwd
}

run_as_user() {
    local username="$1"
    local home_dir="$2"
    shift 2
    local user_path="$home_dir/.local/bin:/usr/local/bin:/usr/bin:/bin"
    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        runuser -u "$username" -- env PATH="$user_path:$PATH" HOME="$home_dir" "$@"
    else
        env PATH="$user_path:$PATH" HOME="$home_dir" "$@"
    fi
}

show_status() {
    echo "=== System-Wide Cache Status ==="
    if [[ -d /var/cache/pacman/pkg ]]; then
        local pac_out pac_sz
        pac_out=$(du -sh /var/cache/pacman/pkg 2>/dev/null || true)
        pac_sz=$(echo "$pac_out" | cut -f1)
        echo "Pacman Cache:           ${pac_sz:-0B}"
    fi

    local j_out
    j_out=$(journalctl --disk-usage 2>/dev/null || true)
    if [[ -n "$j_out" ]]; then
        local j_sz
        j_sz=$(echo "$j_out" | sed 's/^Archived and active journals take up //; s/ in the file system\.$//')
        echo "Journal Size:           ${j_sz:-0B}"
    else
        echo "Journal Size:           N/A"
    fi

    if [[ -d /var/lib/systemd/coredump ]]; then
        local core_sz
        core_sz=$(du -sh /var/lib/systemd/coredump 2>/dev/null | cut -f1 || true)
        echo "Coredumps (/var/lib):   ${core_sz:-0B}"
    fi

    local fp_size="0B"
    local fp_paths=( /var/tmp/flatpak-cache-* /var/lib/flatpak/repo/tmp )
    local valid_fp=()
    for p in "${fp_paths[@]}"; do
        if [[ -e "$p" ]]; then
            valid_fp+=( "$p" )
        fi
    done
    if [[ ${#valid_fp[@]} -gt 0 ]]; then
        local du_fp
        du_fp=$(du -csh "${valid_fp[@]}" 2>/dev/null || true)
        fp_size=$(echo "$du_fp" | tail -n 1 | cut -f1)
    fi
    echo "Flatpak Temp/Staging:   ${fp_size:-0B}"

    if command -v docker &>/dev/null && docker info >/dev/null 2>&1; then
        local d_df
        d_df=$(docker system df --format "{{.Type}}: {{.Size}} ({{.Reclaimable}} reclaimable)" 2>/dev/null || true)
        echo "Docker Status:"
        if [[ -n "$d_df" ]]; then
            echo "$d_df" | sed 's/^/  • /'
        else
            echo "  • Docker daemon active"
        fi
    else
        echo "Docker Status:          Inactive / Not installed"
    fi

    echo ""
    echo "=== User Cache Status ==="
    while IFS=: read -r uname uid uhome; do
        [[ -d "$uhome" ]] || continue
        echo "User: $uname ($uhome)"

        # Yay
        if [[ -d "$uhome/.cache/yay" ]]; then
            local yay_sz
            yay_sz=$(du -sh "$uhome/.cache/yay" 2>/dev/null | cut -f1 || true)
            echo "  • Yay AUR Cache:      ${yay_sz:-0B}"
        fi

        # Trash
        if [[ -d "$uhome/.local/share/Trash" ]]; then
            local trash_sz
            trash_sz=$(du -sh "$uhome/.local/share/Trash" 2>/dev/null | cut -f1 || true)
            echo "  • Trash:              ${trash_sz:-0B}"
        fi

        # Thumbnails
        if [[ -d "$uhome/.cache/thumbnails" ]]; then
            local thumb_sz
            thumb_sz=$(du -sh "$uhome/.cache/thumbnails" 2>/dev/null | cut -f1 || true)
            echo "  • Thumbnails:         ${thumb_sz:-0B}"
        fi

        # Dev Caches
        local dev_info=()
        if [[ -d "$uhome/.cache/uv" ]]; then
            local uv_sz
            uv_sz=$(du -sh "$uhome/.cache/uv" 2>/dev/null | cut -f1 || true)
            dev_info+=( "uv: $uv_sz" )
        fi
        if [[ -d "$uhome/.cache/pip" ]]; then
            local pip_sz
            pip_sz=$(du -sh "$uhome/.cache/pip" 2>/dev/null | cut -f1 || true)
            dev_info+=( "pip: $pip_sz" )
        fi
        if [[ -d "$uhome/.cache/go-build" ]]; then
            local go_sz
            go_sz=$(du -sh "$uhome/.cache/go-build" 2>/dev/null | cut -f1 || true)
            dev_info+=( "go-build: $go_sz" )
        fi
        if [[ ${#dev_info[@]} -gt 0 ]]; then
            local IFS_BAK="$IFS"
            IFS=", "
            echo "  • Dev Caches:         ${dev_info[*]}"
            IFS="$IFS_BAK"
        fi
    done < <(get_human_users)

    echo ""
    echo "=== Systemd Timer Status ==="
    if systemctl is-active --quiet system-autoclean.timer 2>/dev/null; then
        echo "Timer: ACTIVE"
        systemctl list-timers --no-pager system-autoclean.timer 2>/dev/null || true
    else
        echo "Timer: INACTIVE (or not installed)"
    fi

    echo ""
    echo "=== Last Cleanup Log ==="
    journalctl -u system-autoclean.service -n 5 --no-pager 2>/dev/null || echo "No logs found."
}

clean_flatpak() {
    echo "[1/5] Flatpak Cleanup..."
    if ! command -v flatpak &>/dev/null; then
        echo "  Flatpak is not installed, skipping."
        return 0
    fi

    if (( DRY_RUN )); then
        echo "  [Dry Run] Would uninstall unused Flatpak runtimes/extensions."
        echo "  [Dry Run] Would run flatpak repair to prune OSTree objects."
        echo "  [Dry Run] Would purge /var/tmp/flatpak-cache-* and /var/lib/flatpak/repo/tmp/*."
        return 0
    fi

    echo "  Removing unused runtimes and extensions..."
    flatpak uninstall --unused -y --noninteractive 2>&1 | sed 's/^/    /' || true

    echo "  Pruning dangling OSTree objects..."
    flatpak repair 2>&1 | sed 's/^/    /' || true

    echo "  Clearing Flatpak staging and transaction caches..."
    rm -rf /var/tmp/flatpak-cache-* /var/lib/flatpak/repo/tmp/* 2>/dev/null || true
}

clean_pacman() {
    echo "[2/5] Pacman Cache Cleanup..."
    if command -v paccache &>/dev/null; then
        if (( DRY_RUN )); then
            echo "  [Dry Run] Pacman installed packages (keeping last 1 version):"
            paccache -d -k1 2>&1 | sed 's/^/    /' || true
            echo "  [Dry Run] Pacman uninstalled packages (purging all):"
            paccache -d -u -k0 2>&1 | sed 's/^/    /' || true
        else
            echo "  Pruning installed package cache (retaining last 1 version for rollback)..."
            paccache -r -k1 2>&1 | sed 's/^/    /' || true
            echo "  Purging uninstalled package cache..."
            paccache -r -u -k0 2>&1 | sed 's/^/    /' || true
        fi
    else
        echo "  paccache utility not found, falling back to pacman -Sc..."
        if (( ! DRY_RUN )); then
            pacman -Sc --noconfirm 2>&1 | sed 's/^/    /' || true
        fi
    fi
}

clean_docker() {
    echo "[3/5] Docker Engine Cleanup..."
    if ! command -v docker &>/dev/null; then
        echo "  Docker is not installed, skipping."
        return 0
    fi

    if ! docker info >/dev/null 2>&1; then
        echo "  Docker daemon is not running, skipping."
        return 0
    fi

    if (( DRY_RUN )); then
        echo "  [Dry Run] Would prune stopped containers, dangling images, and build cache (preserving named volumes)."
        docker system df 2>&1 | sed 's/^/    /' || true
        return 0
    fi

    echo "  Pruning stopped containers, dangling images, and build cache (preserving named volumes)..."
    docker system prune -f 2>&1 | sed 's/^/    /' || true
}

clean_system_and_logs() {
    echo "[4/5] System Logs & Coredumps..."
    if command -v journalctl &>/dev/null; then
        if (( DRY_RUN )); then
            echo "  [Dry Run] Would vacuum journal logs to 100M and max 14 days."
            echo "  Current journal usage: $(journalctl --disk-usage 2>/dev/null || echo 'unknown')"
        else
            echo "  Vacuuming system journal..."
            journalctl --vacuum-size=100M --vacuum-time=14d 2>&1 | sed 's/^/    /' || true
        fi
    fi

    # Prune coredumps older than 14 days
    if [[ -d /var/lib/systemd/coredump ]]; then
        if (( DRY_RUN )); then
            local count
            count=$(find /var/lib/systemd/coredump -type f -mtime "+$COREDUMP_DAYS" 2>/dev/null | wc -l)
            echo "  [Dry Run] Would delete $count coredump files older than $COREDUMP_DAYS days."
        else
            echo "  Removing coredumps older than $COREDUMP_DAYS days..."
            find /var/lib/systemd/coredump -type f -mtime "+$COREDUMP_DAYS" -delete 2>/dev/null || true
        fi
    fi

    if (( DRY_RUN )); then
        echo "  [Dry Run] Would execute systemd-tmpfiles --clean."
        echo "  [Dry Run] Would remove stale files older than 7 days in /var/tmp."
    else
        if command -v systemd-tmpfiles &>/dev/null; then
            systemd-tmpfiles --clean 2>/dev/null || true
        fi
        find /var/tmp -mindepth 1 -maxdepth 2 -atime +7 -delete 2>/dev/null || true
    fi
}

clean_user_caches() {
    echo "[5/5] User Caches (Trash, Thumbnails, AUR, Dev Tools)..."
    while IFS=: read -r uname uid uhome; do
        [[ -d "$uhome" ]] || continue
        echo "  Processing user: $uname"

        # 1. Yay AUR cache
        local yay_dir="$uhome/.cache/yay"
        if [[ -d "$yay_dir" ]]; then
            if (( DRY_RUN )); then
                echo "    [Dry Run] Would prune build artifacts (src/, pkg/, source archives) in $yay_dir."
                echo "    [Dry Run] Would retain last 1 built package version."
            else
                echo "    Pruning Yay build artifacts and retaining last 1 package version..."
                find "$yay_dir" -mindepth 2 -maxdepth 3 -type d \( -name "src" -o -name "pkg" \) -prune -exec rm -rf {} + 2>/dev/null || true
                find "$yay_dir" -type f \( -name "*.deb" -o -name "*.tar.*" -o -name "*.zip" -o -name "*.iso" \) -delete 2>/dev/null || true
                if command -v paccache &>/dev/null; then
                    for pkg_dir in "$yay_dir"/*; do
                        if [[ -d "$pkg_dir" ]]; then
                            paccache -q -r -k1 -c "$pkg_dir" 2>/dev/null || true
                            paccache -q -r -u -k0 -c "$pkg_dir" 2>/dev/null || true
                        fi
                    done
                fi
            fi
        fi

        # 2. Trash cleanup (>14 days)
        local trash_files="$uhome/.local/share/Trash/files"
        local trash_info="$uhome/.local/share/Trash/info"
        if [[ -d "$trash_files" ]]; then
            local stale_trash
            stale_trash=$(find "$trash_files" -mindepth 1 -maxdepth 1 -mtime "+$TRASH_DAYS" 2>/dev/null | wc -l)
            if (( DRY_RUN )); then
                echo "    [Dry Run] Would empty $stale_trash Trash items older than $TRASH_DAYS days."
            else
                echo "    Purging Trash items older than $TRASH_DAYS days ($stale_trash candidates)..."
                while IFS= read -r -d '' t_file; do
                    local bname
                    bname=$(basename "$t_file")
                    rm -rf "$t_file" 2>/dev/null || true
                    rm -f "$trash_info/$bname.trashinfo" 2>/dev/null || true
                done < <(find "$trash_files" -mindepth 1 -maxdepth 1 -mtime "+$TRASH_DAYS" -print0 2>/dev/null)
            fi
        fi

        # 3. Desktop Thumbnails (>14 days & failed)
        local thumb_dir="$uhome/.cache/thumbnails"
        if [[ -d "$thumb_dir" ]]; then
            if (( DRY_RUN )); then
                echo "    [Dry Run] Would purge failed thumbnails and thumbnails older than $THUMBNAIL_DAYS days."
            else
                echo "    Cleaning stale thumbnail cache..."
                rm -rf "$thumb_dir/fail"/* 2>/dev/null || true
                find "$thumb_dir" -mindepth 2 -type f -atime "+$THUMBNAIL_DAYS" -delete 2>/dev/null || true
            fi
        fi

        # 4. Developer Caches (pip, uv, go)
        # uv
        local uv_bin=""
        if [[ -x "$uhome/.local/bin/uv" ]]; then
            uv_bin="$uhome/.local/bin/uv"
        elif command -v uv &>/dev/null; then
            uv_bin="$(command -v uv)"
        fi
        if [[ -n "$uv_bin" && -d "$uhome/.cache/uv" ]]; then
            if (( DRY_RUN )); then
                echo "    [Dry Run] Would run 'uv cache clean' for $uname."
            else
                echo "    Cleaning uv cache..."
                run_as_user "$uname" "$uhome" "$uv_bin" cache clean 2>&1 | sed 's/^/      /' || true
            fi
        fi

        # pip
        if command -v pip &>/dev/null && [[ -d "$uhome/.cache/pip" ]]; then
            if (( DRY_RUN )); then
                echo "    [Dry Run] Would run 'pip cache purge' for $uname."
            else
                echo "    Purging pip wheel cache..."
                run_as_user "$uname" "$uhome" pip cache purge 2>&1 | sed 's/^/      /' || true
            fi
        fi

        # go
        if command -v go &>/dev/null && [[ -d "$uhome/.cache/go-build" ]]; then
            if (( DRY_RUN )); then
                echo "    [Dry Run] Would run 'go clean -cache' for $uname."
            else
                echo "    Cleaning go build cache..."
                run_as_user "$uname" "$uhome" go clean -cache 2>&1 | sed 's/^/      /' || true
            fi
        fi

    done < <(get_human_users)
}

main() {
    parse_args "$@"

    if (( STATUS_ONLY )); then
        show_status
        exit 0
    fi

    if (( ! DRY_RUN )) && [[ ${EUID:-$(id -u)} -ne 0 ]]; then
        echo "Error: system-autoclean must be run as root (or via sudo/systemd) to perform cleanups." >&2
        echo "Run with --dry-run to preview without root privileges." >&2
        exit 1
    fi

    # Concurrency guard
    if (( ! DRY_RUN )); then
        exec 200>"$LOCK_FILE"
        if ! flock -n 200; then
            echo "Another instance of system-autoclean is currently executing. Exiting." >&2
            exit 1
        fi
    fi

    local start_kb end_kb diff_kb
    start_kb=$(get_free_kb)

    echo "=========================================="
    echo "Starting Comprehensive Autoclean: $(date '+%Y-%m-%d %H:%M:%S')"
    if (( DRY_RUN )); then
        echo "MODE: SIMULATION (Dry Run)"
    fi
    echo "=========================================="

    clean_flatpak
    clean_pacman
    clean_docker
    clean_system_and_logs
    clean_user_caches

    end_kb=$(get_free_kb)
    diff_kb=$(( end_kb - start_kb ))

    echo "=========================================="
    if (( DRY_RUN )); then
        echo "Dry run simulation complete. No files were deleted."
    else
        if (( diff_kb > 0 )); then
            echo "Cleanup complete. Disk space reclaimed: $(format_kb "$diff_kb")"
        else
            echo "Cleanup complete. System caches are already lean."
        fi
    fi
    echo "=========================================="
}

main "$@"
