# system-autoclean

Automated maintenance daemon and systemd timer for Arch Linux systems. Cleans Flatpak unused packages and staging data, Pacman package caches (keeping the last 1 version for offline rollback), AUR (yay) build caches, systemd journal logs, and stale temporary files.

## Scheduling

- **Every Restart**: Runs automatically 5 minutes after system boot (`OnBootSec=5min`) to prevent boot-time disk I/O bottlenecks.
- **Every 4 Days**: Runs periodically (`OnUnitActiveSec=4d`).
- **Persistent Catch-up**: `Persistent=true` ensures missed triggers while the machine is powered off run upon the next boot.
- **Background Execution**: Configured with `IOSchedulingClass=idle` and `CPUSchedulingPolicy=idle` to ensure zero impact on desktop responsiveness, gaming, or build workloads.

## Cleanup Scope

1. **Flatpak**:
   - `flatpak uninstall --unused -y --noninteractive` (removes unreferenced runtimes and extensions).
   - `flatpak repair` (prunes corrupt and dangling OSTree objects).
   - Purges staging and download caches (`/var/tmp/flatpak-cache-*`, `/var/lib/flatpak/repo/tmp/*`).
2. **Pacman & AUR (yay)**:
   - `paccache -r -k1` (retains the latest 1 package version in `/var/cache/pacman/pkg` for safe rollback).
   - `paccache -r -u -k0` (purges uninstalled packages).
   - Prunes user `~/.cache/yay/` directories (removes `src/`, `pkg/`, and source tarballs while keeping the latest package).
3. **Docker Engine**:
   - `docker system prune -f` (removes stopped containers, dangling images, and build cache; preserves named persistent volumes).
4. **Journal & System**:
   - `journalctl --vacuum-size=100M --vacuum-time=14d`.
   - Prunes `/var/lib/systemd/coredump` older than 14 days.
   - `systemd-tmpfiles --clean` and stale `/var/tmp/` files older than 7 days.
5. **User Caches (auto-detected for human users UID >= 1000)**:
   - **Trash**: Empties items in `~/.local/share/Trash` older than 14 days and cleans metadata.
   - **Thumbnails**: Cleans failed and stale thumbnails (`~/.cache/thumbnails`) older than 14 days.
   - **Developer Tool Caches**: Runs `uv cache clean`, `pip cache purge`, and `go clean -cache` under user identity.

## CLI Usage

```bash
# Preview what will be cleaned without modifying files
system-autoclean --dry-run

# Check current cache sizes, timer status, and recent journal log
system-autoclean --status

# Run manual cleanup immediately (requires root)
sudo system-autoclean
```

## Installation

```bash
cd /home/nandha/Desktop/TG_TOOL/scripts/system-autoclean
sudo bash install.sh
```

## Uninstallation

```bash
cd /home/nandha/Desktop/TG_TOOL/scripts/system-autoclean
sudo bash uninstall.sh
```
