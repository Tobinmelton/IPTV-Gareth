# G-IPTV — Project Scope

**In-house IPTV server with a curated channel list.** Private, LAN-only. No cloud, no subscriptions.

> **Direction update (2026-11-09):** TV-browser delivery (Xbox Edge, Roku browser)
> is **abandoned** — those browsers kill long-lived local requests and offer no
> workable remote navigation. Targets are now **PC/PC-browser on the LAN** and a
> **sideloaded APK on the Onn 4K (Android TV)**. See [PLAN.md](PLAN.md) for the
> full plan/spec and the fullscreen work that shipped with this change.

---

## 1. Vision

A personal "cable company" for one household: a small curated list of channels
(home movies, digitized tapes, house cameras, downloaded media) served over the
local network, watchable on a PC browser and Android-TV devices via a sideloaded
app — with a retro-2000s TV-guide interface.

## 2. Step 1 — Framework (current phase)

- **Exactly 2 channels** to prove the pipeline end-to-end before scaling.
- One server box on the LAN does everything: transcoding, packaging, UI hosting.
- Retro UI (this is what we are designing **now**):
  - **Top-left:** channel description panel.
  - **Top-right:** video screen styled like a 2000s-era TV (CRT bezel, scanlines).
  - **Bottom:** scrollable IPTV guide (channels × time slots).

## 3. Delivery format — options

| Option | Roku | Xbox | Google TV | Browser | Notes |
|---|---|---|---|---|---|
| **HLS** (`.m3u8` + segments) | ✅ native | ✅ via Edge / Media Player | ✅ native (ExoPlayer) | ✅ via hls.js | **Recommended.** Universal, supports live + VOD, trivial to generate with ffmpeg |
| DASH (`.mpd`) | ✅ | ✅ | ✅ native | ✅ via dash.js | Equal to HLS, but HLS has broader legacy support |
| Progressive MP4 | ✅ | ✅ | ✅ | ✅ | VOD only, no live "channel" feel |
| DLNA / UPnP | ⚠️ limited | ✅ | ⚠️ | ❌ | No custom UI — devices use their own player |
| RTSP / UDP multicast | ❌ | ❌ | ⚠️ apps only | ❌ | Not usable for our targets |

**Recommendation: HLS for the streams + an HTML5 UI for the guide.**
An M3U playlist (`http://server/channels.m3u`) makes the server instantly
compatible with third-party IPTV player apps on every platform as a bonus.

## 4. Client strategy — options

1. **HTML5 web app (now):** one responsive page, works in Edge on Xbox, any
   browser, any phone. Cheapest path, single codebase.
2. **APK later:** wrap the same UI with Capacitor/Cordova, or build a native
   Android TV app (ExoPlayer + WebView guide) for Google TV / Fire TV sticks.
3. **Roku later:** Roku cannot run APKs or full web apps — options are (a) a
   BrightScript sideloaded channel, or (b) any free IPTV-player channel pointed
   at our M3U. The M3U keeps Roku unblocked at near-zero cost.

## 5. Server stack (proposed, decided after UI)

- **ffmpeg** — transcode/remux sources into HLS (live loop for "channels").
- **Node.js (Express)** or **Python (FastAPI)** — serves UI, M3U, JSON guide API.
- **Guide data:** simple JSON file for Step 1; SQLite later.
- Alternative all-in-one: **MediaMTX** or **Tvheadend** if we outgrow custom.

## 6. Roadmap

- **Phase 1 (now):** UI design — 6 mockups → pick elements → final design.
- **Phase 2:** Static HTML/CSS/JS build of final design with fake JSON data.
- **Phase 3:** Server — 2 real HLS channels + guide API; UI wired to live data.
- **Phase 4:** Platform packaging (APK for Google TV, Roku channel or M3U route).
- **Phase 5+ (future):** more channels, DVR/recordings, now-next overlay, remote
  control, channel logos, parental lock, user profiles.

## 7. Step-1 acceptance criteria

- Two devices of different brands can play both channels at once.
- Guide UI loads in under 2 seconds on the LAN.
- Switching channels feels like channel-surfing a 2000s TV.

## 8. Mockups

Six UI candidates live in [../mockups/index.html](../mockups/index.html).
Open it in a browser to browse them side by side.
