# G-IPTV Server

Python (FastAPI) server that hosts the UI and proxies the IPTV playlist,
streams, and guide data — everything same-origin, so no CORS or
mixed-content issues on the LAN.

## What it provides

| Endpoint | Purpose |
|---|---|
| `GET /` | the UI (stage12, server-adapted) |
| `GET /playlist.m3u` | proxied + cached upstream M3U |
| `GET /api/channels` | parsed channel list as JSON |
| `GET /api/guide` | per-channel now/next program info (XMLTV-backed) |
| `GET /api/curate?category=X&res=Y` | server-side availability scan (accurate — no browser CORS) |
| `GET /stream?url=...` | stream proxy (bypasses hotlink protection / CORS / mixed-content) |

## Run

## Install — any Linux

**One-liner (recommended):**

```bash
bash /path/to/G-IPTV/server/install.sh
```

That creates a self-contained virtualenv at `server/.venv`, installs the
dependencies, and puts a `giptv` command on your PATH. Open a **new
terminal** afterwards, then from anywhere:

```bash
giptv
```

The launcher is self-healing: if the environment is ever deleted, `giptv`
re-runs the installer before starting.

### If the installer can't create the environment

Debian/Ubuntu systems sometimes lack the venv/pip bootstrap pieces. Install
them once with your package manager, then re-run `install.sh`:

```bash
# Debian / Ubuntu / Mint
sudo apt install -y python3 python3-venv python3-pip curl

# Fedora / RHEL / CentOS
sudo dnf install -y python3 python3-pip curl

# Arch / Manjaro
sudo pacman -S --noconfirm python python-pip curl
```

(If you'd rather not use sudo, `install.sh` falls back to fetching pip
automatically — no system changes needed.)

### Manual install (no installer)

```bash
python3 -m venv server/.venv
server/.venv/bin/python -m ensurepip --upgrade   # or: curl get-pip.py
server/.venv/bin/python -m pip install -r server/requirements.txt
server/.venv/bin/python server/main.py
```

Then open `http://<this-machine-ip>:8484` from any device on the LAN.

## Behavior notes

- **CURATE** runs a server-side availability scan of the current view and
  shows live progress. Starting a scan **stops any currently tuned channel**
  (audio/video) so nothing keeps playing underneath.
- Dead channels sink to the bottom of the guide; favorited channels stay
  pinned at the top whether alive or not.

Config via env vars: `GIPTV_PORT` (default 8484), `GIPTV_PLAYLIST_URL`,
`GIPTV_EPG_URL` (XMLTV source, optional).
