# Rclone AutoSync (`rasync`)

A modern, high-performance, automated command-line interface (CLI) and headless background service for synchronizing local folders to Google Drive (and other remotes) via `rclone`.

Built with Python, [Rich](https://github.com/Textualize/rich), [Questionary](https://github.com/tmbo/questionary), [Watchdog](https://github.com/gorakhargosh/watchdog), and Linux `systemd --user`.

---

## ✨ Features

- **⚡ Fast, Modern CLI**: Clean Rich terminal interface with color-coded status tables, live multi-bar progress displays, and completion summary cards.
- **🧙 Interactive Setup Wizards**: `rasync add` and `rasync edit` auto-discover your `rclone` remotes (e.g. `Trietn31`) and guide you through selecting cloud paths, modes, and triggers.
- **🔄 Sync Modes**:
  - `sync`: 1-way mirror (Google Drive matches local directory; deleted files safely moved to Google Drive Trash).
  - `copy`: 1-way upload (uploads new & changed files, preserves extras on remote).
  - `bisync`: 2-way bidirectional sync with automatic `--resync` baseline handling and `--recover` / `--resilient` flags.
- **🩺 Built-in System Doctor (`rasync doctor`)**: Automatically validates rclone installation, cloud remotes, power/battery state, and checks Linux `inotify` queue limits against your directory sizes with actionable fix recommendations.
- **🔌 Smart Charger & Battery Triggers**: Trigger synchronizations the moment your laptop charger is plugged in (`sync_on_ac`), and pause background syncs while running on battery power (`pause_on_battery`) to protect battery life and mobile hotspot data.
- **👀 Foreground Event Watcher (`rasync watch`)**: Live terminal event stream showing real-time file changes, debounce countdowns, and sync triggers in your current terminal session.
- **⚙️ Native Linux Service (`systemd --user`)**: Lightweight background service running continuous file watchers and scheduled syncs independently of terminal sessions.
- **🛡️ Google Drive Trash Protection**: Deleted files are moved to Google Drive Trash (`--drive-use-trash`) instead of being permanently lost.
- **🚀 Bandwidth Throttling**: Configure upload limits (`--bwlimit`) globally or per-profile.

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.10+
- `rclone` configured with a remote (e.g. `Trietn31:` or `gdrive:`)
- Linux (`systemd --user` supported)

---

## ☁️ Tutorial: How to Add Your Own Rclone Remote

Before `rasync` can synchronize your files, you need at least one remote configured in `rclone`. Below is a step-by-step walkthrough for setting up Google Drive (or any cloud provider):

### Step 1: Launch the Rclone Config Wizard
Run in your terminal:
```bash
rclone config
```

### Step 2: Create a New Remote
1. Type `n` and press `Enter` for **New remote**.
2. Enter a descriptive name for your remote (e.g., `gdrive`, `drive`, or `Trietn31`).
   > [!TIP]
   > Keep the name short and simple without spaces. `rasync` will auto-discover this name.

### Step 3: Select Storage Type
A list of 50+ supported cloud backends will be displayed:
- Type `drive` (or enter its corresponding number) for **Google Drive**.
- *(Other popular options: `onedrive` for Microsoft OneDrive, `dropbox` for Dropbox, `pcloud` for pCloud, `sftp` for SSH/SFTP).*

### Step 4: Configure Credentials & Permissions
1. **Client ID & Client Secret**: Press `Enter` twice to leave blank (uses rclone's built-in application keys).
2. **Access Scope**: Type `1` and press `Enter` for full access to read, create, and delete files on your Drive (`drive`).
3. **Root Folder ID & Service Account**: Press `Enter` to leave blank for standard user drives.

### Step 5: Complete Authentication
1. **Auto Config**: When prompted `Use auto config? (y/n)`, press `y` (default).
2. A web browser window will open automatically. Sign in with your Google account and click **Allow** to authorize rclone.
   > *(If you are on a remote headless server via SSH, choose `n` and follow the on-screen instructions to run `rclone authorize "drive"` locally).*

### Step 6: Save and Finish
1. When asked `Configure this as a Shared Drive (Team Drive)?`, choose `n` (unless using a Workspace Shared Drive).
2. Confirm the remote configuration by typing `y`.
3. Type `q` to quit the `rclone config` menu.

### Step 7: Verify Your Remote
Test that rclone can reach your cloud drive:
```bash
# List directories in the root of your remote:
rclone lsd gdrive:

# Run rasync doctor to confirm rasync detects your remote:
rasync doctor
```

Once detected, launch `rasync add` to configure automated syncing!

---

### 2. Running with `uv`
```bash
# View help and configured profiles
uv run rasync

# Create a new sync profile interactively
uv run rasync add

# Sync a profile with live visual progress
uv run rasync sync "Profile Name"

# Test a sync without altering files (dry-run simulation)
uv run rasync sync "Profile Name" --dry-run

# Run real-time file watcher in the current terminal window
uv run rasync watch "Profile Name"

# Run system health and inotify diagnostic checks
uv run rasync doctor
```

### 3. Installing as a Global User Command
To install `rasync` directly into your `$PATH`:
```bash
uv tool install .
# or
pipx install .
```
Then simply run `rasync` anywhere!

---

## 📖 CLI Command Reference

### Profile Management
| Command | Description |
| :--- | :--- |
| `rasync add` (or `new`) | Launch interactive wizard to configure a new sync profile |
| `rasync list` (or `status`, `ls`) | Display rich table of all profiles, modes, triggers, and last sync states |
| `rasync edit <name>` | Interactively update an existing profile |
| `rasync remove <name>` (or `rm`) | Delete a profile (`-y` to skip confirmation) |

### Sync & Watch Operations
| Command | Description |
| :--- | :--- |
| `rasync sync <name>` | Synchronize profile with live progress bar, speed meter, ETA, and active files |
| `rasync sync --all` | Sequentially synchronize all enabled profiles |
| `rasync sync <name> --dry-run` | Preview files to be transferred or trashed without making changes |
| `rasync sync <name> --resync` | Perform first-time baseline synchronization for `bisync` profiles |
| `rasync monitor` (or `mon`, `top`) | Live continuous terminal task bar & dashboard for background syncs (`--once` for snapshot) |
| `rasync watch <name>` | Run real-time foreground file watcher with live change feed |

### System & Service Controls
| Command | Description |
| :--- | :--- |
| `rasync doctor` | Check rclone binary, cloud remotes, and Linux inotify queue capacity |
| `rasync logs [-f] [-n 50]` | View background service logs (`-f` to follow in real-time) |
| `rasync service install` | Install and enable the background systemd user service |
| `rasync service status` | View systemd user service health |
| `rasync service start` / `stop` / `restart` | Control background auto-sync service |

---

## 📁 Configuration & Data Paths

- **Profiles**: `~/.config/rasync/profiles.json` *(plain JSON, directly editable)*
- **Global Settings**: `~/.config/rasync/config.json`
- **Sync History**: `~/.config/rasync/history.json`
- **Daemon Logs**: `~/.config/rasync/daemon.log`
- **Systemd Unit**: `~/.config/systemd/user/rasync.service`

---

## ⚡ Charger & Power Management

`rasync` includes native Linux battery and AC charger detection (via `/sys/class/power_supply`):

- **Sync on Charger Plugged In (`sync_on_ac`)**:
  When you connect your laptop to wall power / charger, `rasync` immediately triggers a sync for all profiles configured with this setting. Perfect for syncing heavy work right when returning to your desk.
- **Pause on Battery (`pause_on_battery`)**:
  Prevents automatic file watch and scheduled sync triggers from consuming CPU and bandwidth while working on battery power.

Configure these interactively during `rasync add` / `rasync edit`, or directly set `"sync_on_ac": true` and `"pause_on_battery": true` in `~/.config/rasync/profiles.json`.

---

## 🛠️ Linux Kernel Inotify Tuning

If you watch large directory trees (such as whole drives with >16,000 subdirectories), Linux's default inotify queue size (`16384`) may overflow under heavy I/O:

```bash
# Check current health and directory counts:
rasync doctor

# Temporarily increase inotify queue size:
sudo sysctl fs.inotify.max_queued_events=131072

# Make permanent in /etc/sysctl.d/99-rasync.conf:
echo "fs.inotify.max_queued_events=131072" | sudo tee /etc/sysctl.d/99-rasync.conf
sudo sysctl --system
```

---

## 🤖 Disclaimer

> [!NOTE]
> This project was **vibe coded by Antigravity** (Google DeepMind's advanced agentic coding assistant) in collaboration with the user. Please remember to test your profiles with `--dry-run` and review sync paths before performing large production transfers. Happy syncing! 🚀
