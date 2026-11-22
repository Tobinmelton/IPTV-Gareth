# G-IPTV — Progress Log

Running record of major progress, findings, and setbacks — good and bad —
so future sessions don't re-discover them. Newest entries at the top.
Keep this updated whenever something significant lands or breaks.

---

## 2026-11-22 — Verbose logging added (investigating curate false-dead)

### Problem being investigated (OPEN)
After a full curate run, channels that the same scan marked **online**
showed **offline** ~15 min later. Not resolved yet — logging added first so
the next occurrence can be diagnosed from evidence instead of guesswork.

**Suspects identified from code reading:**
1. **Aggressive runtime dead-marking** (prime suspect): any *fatal* hls.js
   error while watching — including transient network blips — calls
   `markDead()`, which permanently flags the channel in localStorage AND
   POSTs `/api/dead`, poisoning every device on the LAN. No retry, no
   distinction between "geo-blocked 403" and "one segment timed out".
2. **`/api/dead` boot override**: on every page load the UI re-applies all
   server-reported dead nums *over* fresh curate alive results.
3. **Channel-number instability**: alive/dead sets are keyed by `num`,
   which is just merge order of the playlist. If iptv-org's list changes
   between pulls (hourly TTL / server restart), saved nums point at
   different channels and the sets scramble.
4. `_dead_reported` is **in-memory only** — wiped on server restart
   (observed: after today's restart it returned `[]`).

### Logging infrastructure (shipped)
- `server/logs/server.log` (rotating, 8 MB × 3): playlist/EPG refreshes,
  **every curate probe with verdict + reason + latency + URL**, dead
  reports with client IP, stream-proxy upstream failures (http ≥ 400).
- `server/logs/client.log`: UI batches `clog()` lines to
  `POST /api/clientlog` (works from the APK WebView too). Logs boot
  restore counts, `/api/dead` application, tune attempts, hls fatal and
  non-fatal errors with details, markDead reasons, curate merges
  (before/after set sizes), guide refreshes, js errors.
- Verified live: small scan (10 targets) logged 7 alive / 3 dead with
  reasons (`http 403` on trace.plus = geo-block); page reload logged boot
  restore `alive=6872 dead=4640` from the 13:33 full scan.
- Fixed while in there: duplicated `if __name__ == "__main__"` block at
  the bottom of main.py (second copy would restart the server after
  Ctrl-C).

### Pre-existing bug spotted (not yet fixed)
M3U parser breaks on EXTINF lines whose attribute values contain commas:
channels named `like Gecko) Chrome/156.0.0.0 Safari/537.36" group-…`
(user-agent leaking into the name). `parse_m3u` finds the first `,` —
should find the comma *after* the last quoted attribute.

---

## 2026-11-22 — Phase C: EPG guide data (DONE)

- **Found & fixed a real bug**: XMLTV timestamps (`20261122120000 +0000`)
  were parsed as *local* time — every program was ~6 h off in US Central.
  Now the ±hhmm offset is honored (missing offset = UTC).
- `server/epg.txt` lists XMLTV sources (same format as sources.txt).
  Default: free daily dump from https://epgshare01.online/epgshare01/
  (`epg_ripper_US2.xml.gz`). `GIPTV_EPG_URL` env var is the fallback.
- **Fuzzy channel matching**: iptv-org tvg-ids (`ESPN2.us@SD`) never equal
  free-XMLTV ids (`ESPN2.HD.us2`) — both sides reduce to lowercase
  alphanumeric keys with country/resolution decorations stripped, and
  XMLTV `<display-name>`s join the alias pool.
- **RAM guard**: only programmes for playlist channels within −2 h…+48 h
  are kept; iterparse memory kept flat (`el.clear()` + `del root[:]`);
  asyncio lock prevents concurrent guide downloads.
- `/api/guide` → `{now, next, later, now_start, now_stop, next_start}`,
  keyed by tvg-id with `num:<n>` fallback. UI fills NOW/NEXT/LATER,
  shows program times in the info panel, re-fetches every 5 min.
- **Verified live**: 11,512 channels + US2 guide → 232 EPG channels
  matched → 393 guide entries, times correct. Server RSS ~182 MB.
- **Limitation**: coverage is US-centric by default (232/11512 channels);
  add regional files in epg.txt for more. Matching is name-based and can
  in principle mismatch similarly-named channels.

---

## Earlier (from git history / PLAN.md)

- **APK compiled** (`49ba08c`) — Capacitor WebView thin client for the
  Onn 4K. Boots to a setup screen, saves server IP, D-pad drives UI keys.
- **Architecture locked — Option 1 thin client**: server (FastAPI,
  port 8484) does playlist merge, stream proxying (`/stream` rewrites HLS
  manifests), curate scans, dead-list. Devices only render + play.
  Known tradeoff: Onn is a brick if the PC is off.
- **Fullscreen (U key / ⛶)** ships video-only fullscreen.
- **Curate** runs as a server-side background job (24-way concurrency,
  8 s timeout, 8 KB head probes) with live polling; was moved server-side
  after browser-side probing proved inaccurate (CORS) and RAM-heavy.
- **Dropped**: Xbox/Roku browser targets (throttle/kill long-lived local
  requests), native Roku channel.
