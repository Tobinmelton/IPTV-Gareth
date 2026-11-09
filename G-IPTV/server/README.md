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

```bash
pip install -r server/requirements.txt
python server/main.py
```

Then open `http://<this-machine-ip>:8484` from any device on the LAN.

Config via env vars: `GIPTV_PORT` (default 8484), `GIPTV_PLAYLIST_URL`,
`GIPTV_EPG_URL` (XMLTV source, optional).
