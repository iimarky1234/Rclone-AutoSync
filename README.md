# Rclone AutoSync (`rasync`)

A modern, high-performance Terminal User Interface (TUI) and headless background service for synchronizing local files and folders to Google Drive (and other remotes) via `rclone`.

Built with Python, [Textual](https://textual.textualize.io/), [Watchdog](https://github.com/gorakhargosh/watchdog), and Linux `systemd --user`.

---

## ✨ Features

- **📊 Modern Textual TUI**: Reactive dashboard with tabs for profile management, active file transfer cards, dual-pane local/cloud folder explorer, and daemon/service controls.
- **⚡ Real-time Auto-Sync**: Recursive file change watcher with smart debounce quiet periods (default 10s) to batch rapid file edits and compilations.
- **🔄 Sync Modes**:
  - `sync`: 1-way mirror (Google Drive matches local directory; deleted files safely moved to Google Drive Trash).
  - `copy`: 1-way upload (uploads new & changed files, preserves extras on remote).
  - `bisync`: 2-way bidirectional sync.
- **🛡️ Safety & Dry-Run**: Built-in Google Drive Trash protection (`--drive-use-trash`) and full interactive **Dry-Run Preview** dialog before committing changes.
- **🚀 Bandwidth Throttling**: Configure upload bandwidth limits (`--bwlimit`) globally or per-profile (e.g. `5M`, `10M`, or unlimited).
- **⚙️ Native systemd Integration**: Runs as a lightweight headless background service (`systemd --user`). Closing the TUI never interrupts background file watchers or active transfers.
- **🔔 Desktop Notifications**: Linux desktop notifications (via `notify-send`) for sync errors, token issues, and task completions.
- **📁 Google Drive Explorer**: Browse remote Google Drive folders interactively directly from the TUI to select sync targets.

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.10+
- `rclone` configured with your Google Drive remote (e.g. `Trietn31:`)
- Linux (`systemd --user` supported)

### 2. Running with `uv` (Recommended)
```bash
# Launch interactive TUI
uv run rasync

# Check profiles and service status from CLI
uv run rasync status

# Trigger a sync directly from terminal
uv run rasync sync "My Documents Backup" --dry-run
```

### 3. Installing as a CLI Tool
To install `rasync` globally for your user environment:
```bash
uv tool install .
# or
pipx install .
```
Then simply run:
```bash
rasync
```

---

## 🛠️ Background Service Setup (`systemd`)

You can install and enable the background service in one step, either from inside the TUI's **Daemon & Logs** tab, or via the CLI:

```bash
# Install and enable the systemd user service
rasync service install

# Check status
rasync service status

# View live service journal logs
rasync service logs

# Stop or restart
rasync service stop
rasync service restart
```

---

## ⌨️ TUI Keybindings

| Key | Action |
| :--- | :--- |
| `[Space]` | Run Sync Now for selected profile |
| `[D]` | Dry-Run preview (simulate changes and view log diff) |
| `[W]` | Toggle real-time file watcher on/off |
| `[N]` | Create new sync profile (opens wizard dialog) |
| `[E]` | Edit selected sync profile |
| `[Delete]` | Delete selected sync profile |
| `[R]` | Refresh dashboard data & daemon status |
| `[1]` | Switch to **Dashboard** tab |
| `[2]` | Switch to **Drive Explorer** tab |
| `[3]` | Switch to **Daemon & Logs** tab |
| `[Q]` | Quit TUI (daemon continues running in background) |

---

## 📁 Configuration & Data Paths

- **Profiles & Config**: `~/.config/rasync/profiles.json` and `~/.config/rasync/config.json`
- **History & Stats**: `~/.config/rasync/history.json`
- **Daemon Logs**: `~/.config/rasync/daemon.log`
- **IPC Socket**: `~/.config/rasync/rasync.sock`
- **Systemd Service**: `~/.config/systemd/user/rasync.service`
