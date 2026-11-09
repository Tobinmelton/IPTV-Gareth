# G-IPTV — Direction, Plan & Spec (2026-11-09)

## Direction change (locked)

**Abandoned:** running the UI in a TV browser — MS Edge on Xbox, Roku's browser,
etc. They throttle/kill long-lived local requests, have no reliable remote
navigation for a dense guide, and can't be packaged.

**New target:** the UI runs on
1. a **PC / PC-class browser** on the local network, and
2. the **Onn 4K Streaming Device** (Android TV / Google TV) via a **sideloaded APK**.

Server stays a headless LAN service on a PC; devices are thin clients.

## Fullscreen (just shipped)

- **⛶ FULLSCREEN button** on the control strip, or press **U**.
- Full browser fullscreen (Chrome/Edge/Firefox on PC) and, inside the APK
  WebView, it maps to immersive mode (status/nav bars hidden).
- In fullscreen the app goes edge-to-edge (background flush, tighter margins).

## Plan

### Phase A — APK packaging (near-term)
- Wrap `mockups/stage12.html` with **Capacitor** (Android WebView shell).
- Server URL baked in or set on first run (LAN IP / hostname).
- Remote (D-pad) maps to existing keys: ↑↓←→ nav, Enter=select, F=favorite,
  C=curate, U=fullscreen, Back=exit.
- Output: sideloadable `giptv.apk` for the Onn 4K.

### Phase B — TV-native polish
- Focus/outline states that read at 10 feet.
- Number-pad direct channel entry.
- Bigger hit targets on the control strip for D-pad.

### Phase C — guide data
- Point `GIPTV_EPG_URL` at an XMLTV source so NOW/NEXT show real programs.

### Deferred / out
- Xbox / Roku browser support — dropped.
- Native Roku (BrightScript) channel — dropped (device is Android).

## Spec — thin-client contract

| Piece | Detail |
|---|---|
| Server | `server/main.py` (FastAPI) on a LAN PC, port `GIPTV_PORT` (8484) |
| Client | WebView (PC browser / APK) loads `http://<server>:8484` |
| Data | `/api/channels`, `/api/sources`, `/api/guide` |
| Playback | `/stream?url=…` proxied HLS via hls.js |
| Curate | `POST /api/curate/start` → poll `GET /api/curate/status` → `POST /api/curate/stop` |
| Dead list | `POST /api/dead` (runtime geo-block report), `GET /api/dead` |
| State | favorites + curation in `localStorage` per device |
| Keys | ↑↓←→ · Enter · F · C · U · Esc |

## Env vars
`GIPTV_PORT` · `GIPTV_PLAYLIST_URL` (fallback if sources.txt empty) · `GIPTV_EPG_URL`
