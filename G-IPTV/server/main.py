"""
G-IPTV server — FastAPI.

Serves the UI and proxies the playlist, streams, and guide data so that
everything the browser touches is same-origin (no CORS, no mixed content).

Run:  python server/main.py
Then: http://<this-machine-ip>:8484 from any LAN device.
"""

import asyncio
import calendar
import gzip
import io
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               StreamingResponse)

# ---------------------------------------------------------------- config
PORT = int(os.environ.get("GIPTV_PORT", "8484"))
PLAYLIST_URL = os.environ.get(
    "GIPTV_PLAYLIST_URL", "https://iptv-org.github.io/iptv/index.m3u")
EPG_URL = os.environ.get("GIPTV_EPG_URL", "")

UI_FILE = Path(__file__).resolve().parent.parent / "mockups" / "stage12.html"

PLAYLIST_CACHE_TTL = 3600          # re-pull upstream at most hourly
EPG_CACHE_TTL = 6 * 3600           # guide refresh twice a day is plenty
CURATE_CONCURRENCY = 24            # kept modest: RAM-tight machines
CURATE_TIMEOUT = 8.0
PROBE_MAX_BYTES = 8192             # probe reads only the head of a response

SOURCES_FILE = Path(__file__).resolve().parent / "sources.txt"
EPG_FILE = Path(__file__).resolve().parent / "epg.txt"
EPG_PAST_WINDOW = 2 * 3600         # keep programmes ending up to 2 h ago
EPG_FUTURE_WINDOW = 48 * 3600      # … and starting up to 48 h ahead

app = FastAPI(title="G-IPTV")

# ---------------------------------------------------------------- state
_playlist_raw: str = ""
_playlist_fetched: float = 0.0
_channels: list[dict] = []
_sources: list[dict] = []          # [{i, label, url}]
_epg_fetched: float = 0.0
# epg channel id -> list of (start_epoch, stop_epoch, title)
_epg: dict[str, list[tuple[int, int, str]]] = {}
# normalized alias -> epg channel id (for fuzzy playlist↔EPG matching)
_epg_alias: dict[str, str] = {}
_epg_lock: asyncio.Lock = asyncio.Lock()

# shared http client (created on startup, closed on shutdown)
client: httpx.AsyncClient


# ---------------------------------------------------------------- parsing
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def parse_m3u(text: str) -> list[dict]:
    out: list[dict] = []
    meta = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "#EXTM3U":
            continue
        if line.startswith("#EXTINF"):
            meta = {"id": "", "name": "", "logo": "", "group": "", "extras": []}
            for k, v in _ATTR_RE.findall(line):
                k = k.lower()
                if k == "tvg-id":
                    meta["id"] = v
                elif k == "tvg-logo":
                    meta["logo"] = v
                elif k == "group-title":
                    meta["group"] = v
                else:
                    meta["extras"].append(f"{k}={v}")
            comma = line.find(",")
            meta["name"] = line[comma + 1:].strip() if comma >= 0 else "Unknown"
        elif line.startswith("#"):
            continue                       # #EXTVLCOPT etc.
        elif meta is not None:
            meta["url"] = line
            out.append(meta)
            meta = None
    return out


_XMLTV_DATE = re.compile(r"(\d{14})(?:\s*([+-]\d{4}))?")


def _xmltv_epoch(s: str) -> int:
    """XMLTV timestamp → epoch. Honors the ±hhmm offset; assumes UTC if absent."""
    m = _XMLTV_DATE.search(s or "")
    if not m:
        return 0
    utc = calendar.timegm(time.strptime(m.group(1), "%Y%m%d%H%M%S"))
    off = m.group(2)
    if off:
        sign = 1 if off[0] == "+" else -1
        utc -= sign * (int(off[1:3]) * 3600 + int(off[3:5]) * 60)
    return utc


# --- fuzzy playlist↔EPG channel matching -----------------------------------
# tvg-ids ("ESPN2.us@SD") and EPG ids ("ESPN2.HD.us2" / "ESPN2 HD") rarely
# agree verbatim across free sources, so both sides are reduced to a
# lowercase alphanumeric key with country/resolution decorations stripped.
_PAREN_RE = re.compile(r"\([^)]*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_RES_TAIL_RE = re.compile(r"(?:uhd|fhd|hd|sd|4k|2160p|1080p|720p|576p|480p)$")


def _norm(s: str) -> str:
    s = _PAREN_RE.sub("", (s or "").lower())
    return _NON_ALNUM_RE.sub("", s)


def _alias_keys(*names: str) -> set[str]:
    """Normalized keys for a channel, with and without a resolution tail."""
    keys: set[str] = set()
    for n in names:
        k = _norm(n)
        if len(k) < 2:
            continue
        keys.add(k)
        bare = _RES_TAIL_RE.sub("", k)
        if len(bare) >= 2:
            keys.add(bare)
    return keys


def _playlist_aliases(ch: dict) -> set[str]:
    """Matching keys for one playlist channel (tvg-id base + display name)."""
    names = [ch.get("name", "")]
    tvg = (ch.get("id") or "").split("@")[0]          # drop @SD/@HD quality tag
    if tvg:
        parts = tvg.split(".")
        # drop the trailing country token ("ESPN2.us" -> "ESPN2")
        names.append(".".join(parts[:-1]) if len(parts) > 1 else tvg)
    return _alias_keys(*names)


def _epg_id_aliases(cid: str) -> set[str]:
    """Matching keys for an XMLTV channel id ("ESPN2.HD.us2" -> espn2hd/espn2)."""
    parts = cid.split(".")
    base = ".".join(parts[:-1]) if len(parts) > 1 else cid
    return _alias_keys(base, cid)


def _read_sources() -> list[dict]:
    """sources.txt — one M3U URL per line, optional 'URL | Label'."""
    out = []
    if not SOURCES_FILE.exists():
        return out
    for raw in SOURCES_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            url, label = (p.strip() for p in line.split("|", 1))
        else:
            url, label = line, ""
        if url.startswith(("http://", "https://")):
            out.append({"i": len(out), "label": label or url, "url": url})
    return out


async def refresh_playlist(force: bool = False) -> None:
    global _playlist_raw, _playlist_fetched, _channels, _sources
    if not force and time.time() - _playlist_fetched < PLAYLIST_CACHE_TTL and _channels:
        return
    sources = _read_sources()
    if not sources:                      # fall back to the single env URL
        sources = [{"i": 0, "label": "default", "url": PLAYLIST_URL}]
    try:
        async def fetch(src):
            try:
                r = await client.get(src["url"], timeout=30)
                r.raise_for_status()
                chans = parse_m3u(r.text)
                for c in chans:
                    c["src"] = src["i"]
                return chans
            except Exception as e:
                print(f"[sources] {src['label']} failed: {e}")
                return []

        results = await asyncio.gather(*(fetch(s) for s in sources))
        merged = [c for chans in results for c in chans]
        for i, ch in enumerate(merged, 1):
            ch["num"] = i
        _channels = merged
        _sources = sources
        _playlist_raw = ""               # raw merged view not kept (saves RAM)
        _playlist_fetched = time.time()
    except Exception as e:
        # never take the server down over a transient upstream failure;
        # keep serving the previous snapshot if we have one
        print(f"[playlist] refresh failed: {e}")
        if not _channels:
            raise            # only fatal on cold start with no cache


def _read_epg_sources() -> list[str]:
    """epg.txt — one XMLTV URL/path per line; GIPTV_EPG_URL as fallback."""
    out: list[str] = []
    if EPG_FILE.exists():
        for raw in EPG_FILE.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            url = line.split("|", 1)[0].strip()      # optional '| label'
            if url:
                out.append(url)
    if not out and EPG_URL:
        out.append(EPG_URL)
    return out


async def refresh_epg(force: bool = False) -> None:
    """Load XMLTV guides, keeping only programmes for playlist channels
    inside a small time window (RAM-tight machines)."""
    global _epg, _epg_alias, _epg_fetched
    async with _epg_lock:
        if not force and time.time() - _epg_fetched < EPG_CACHE_TTL and _epg:
            return
        urls = _read_epg_sources()
        if not urls:
            return
        await refresh_playlist()
        wanted: set[str] = set()
        for ch in _channels:
            wanted |= _playlist_aliases(ch)

        now = time.time()
        lo, hi = now - EPG_PAST_WINDOW, now + EPG_FUTURE_WINDOW
        epg: dict[str, list[tuple[int, int, str]]] = {}
        alias: dict[str, str] = {}
        kept: dict[str, bool] = {}       # epg cid -> matched a playlist channel

        def feed(raw: bytes) -> None:
            ctx = ET.iterparse(io.BytesIO(raw), events=("start", "end"))
            _, root = next(ctx)
            for event, el in ctx:
                if event != "end":
                    continue
                if el.tag == "channel":
                    cid = el.get("id", "")
                    if cid and cid not in kept:
                        keys = _epg_id_aliases(cid)
                        for dn in el.iter("display-name"):
                            keys |= _alias_keys(dn.text or "")
                        if keys & wanted:
                            kept[cid] = True
                            for k in keys:
                                alias.setdefault(k, cid)
                        else:
                            kept[cid] = False
                elif el.tag == "programme":
                    cid = el.get("channel", "")
                    if kept.get(cid):
                        start = _xmltv_epoch(el.get("start", ""))
                        stop = _xmltv_epoch(el.get("stop", ""))
                        if stop >= lo and start <= hi:
                            t = el.find("title")
                            title = (t.text or "").strip() if t is not None else ""
                            if title:
                                epg.setdefault(cid, []).append((start, stop, title))
                else:
                    continue
                el.clear()
                del root[:]              # keep iterparse memory flat

        for url in urls:
            try:
                if url.startswith(("http://", "https://")):
                    r = await client.get(url, timeout=120)
                    r.raise_for_status()
                    raw = r.content
                else:
                    raw = Path(url).read_bytes()
                if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                feed(raw)
                del raw
            except Exception as e:                   # never take the server down
                print(f"[epg] {url} failed: {e}")

        if epg:
            for lst in epg.values():
                lst.sort()
            _epg = epg
            _epg_alias = alias
            _epg_fetched = time.time()
            print(f"[epg] {len(epg)} channels matched from {len(urls)} source(s)")


# ---------------------------------------------------------------- lifespan
@app.on_event("startup")
async def _startup() -> None:
    global client
    client = httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "G-IPTV/1.0 (+LAN home server)"},
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=60),
    )
    await refresh_playlist()
    asyncio.create_task(refresh_epg())


@app.on_event("shutdown")
async def _shutdown() -> None:
    await client.aclose()


# ---------------------------------------------------------------- routes
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(UI_FILE)


@app.get("/playlist.m3u", include_in_schema=False)
async def playlist():
    await refresh_playlist()
    return PlainTextResponse(_playlist_raw, media_type="audio/x-mpegurl")


@app.get("/api/channels")
async def api_channels():
    await refresh_playlist()
    return JSONResponse(_channels)


@app.get("/api/sources")
async def api_sources():
    await refresh_playlist()
    return JSONResponse(_sources)


@app.get("/api/guide")
async def api_guide():
    """Now/next/later per channel, if an EPG source is configured.

    Keys are the channel tvg-id when present, else "num:<channel number>".
    """
    await refresh_epg()
    now = int(time.time())
    out: dict[str, dict] = {}
    for ch in _channels:
        key = ch.get("id") or f"num:{ch['num']}"
        if key in out:
            continue
        cid = None
        tvg = ch.get("id", "")
        if tvg and tvg in _epg:
            cid = tvg
        else:
            for k in _playlist_aliases(ch):
                cid = _epg_alias.get(k)
                if cid:
                    break
        progs = _epg.get(cid) if cid else None
        if not progs:
            continue
        now_p = None
        upcoming: list[tuple[int, int, str]] = []
        for p in progs:
            if p[0] <= now < p[1]:
                now_p = p
            elif p[0] > now:
                upcoming.append(p)
                if len(upcoming) == 2:
                    break
        if not now_p and not upcoming:
            continue
        entry: dict = {
            "now": now_p[2] if now_p else None,
            "next": upcoming[0][2] if upcoming else None,
            "later": upcoming[1][2] if len(upcoming) > 1 else None,
        }
        if now_p:
            entry["now_start"], entry["now_stop"] = now_p[0], now_p[1]
        if upcoming:
            entry["next_start"] = upcoming[0][0]
        out[key] = entry
    return JSONResponse(out)


def _res_tag(name: str):
    m = re.search(r"\(([^)]*(?:\d{3,4}p|4k|uhd|hd|sd)[^)]*)\)", name, re.I)
    return m.group(1).upper().replace(" ", "") if m else None


# ---------------------------------------------------------------- curate job
# Curate runs as a BACKGROUND JOB so the UI can poll live progress instead
# of blocking on one long HTTP request.
_curate_job: dict = {
    "running": False, "total": 0, "done": 0, "alive": 0, "dead": 0,
    "current": "", "started": 0.0, "finished": False, "stopped": False,
    "alive_nums": [], "dead_nums": [], "ts": 0,
}
_curate_task: asyncio.Task | None = None
_curate_cancel: asyncio.Event | None = None


@app.post("/api/curate/start")
async def curate_start(category: str = Query("ALL"), res: str = Query("__ALL__"),
                       src: int = Query(-1)):
    """Kick off a curate scan. Returns immediately — the scan runs in the
    background and the client polls /api/curate/status. Returning fast
    matters for TV browsers, which kill requests they think have stalled."""
    global _curate_task, _curate_cancel
    if _curate_job["running"]:
        return JSONResponse({"ok": False, "msg": "already running"}, status_code=409)
    await refresh_playlist()

    targets = _channels
    if src >= 0:
        targets = [c for c in targets if c.get("src") == src]
    if category not in ("ALL", "FAVES", "DEAD"):
        targets = [c for c in targets if (c["group"] or "Undefined") == category]
    if res != "__ALL__":
        targets = [c for c in targets if _res_tag(c["name"]) == res]
    if not targets:
        return JSONResponse({"ok": False, "msg": "nothing to scan"}, status_code=400)

    _curate_job.update({
        "running": True, "total": len(targets), "done": 0, "alive": 0,
        "dead": 0, "current": "", "started": time.time(),
        "finished": False, "stopped": False, "alive_nums": [], "dead_nums": [],
    })
    _curate_cancel = asyncio.Event()
    _curate_task = asyncio.create_task(_run_curate(targets))
    return {"ok": True, "total": len(targets)}


async def _run_curate(targets):
    global _curate_job
    sem = asyncio.Semaphore(CURATE_CONCURRENCY)

    async def probe(ch):
        if _curate_cancel.is_set():
            return
        async with sem:
            if _curate_cancel.is_set():
                return
            _curate_job["current"] = ch["name"]
            ok = await _probe_channel(ch)
            if _curate_cancel.is_set():
                return
            _curate_job["done"] += 1
            if ok:
                _curate_job["alive"] += 1
                _curate_job["alive_nums"].append(ch["num"])
            else:
                _curate_job["dead"] += 1
                _curate_job["dead_nums"].append(ch["num"])

    await asyncio.gather(*(probe(c) for c in targets))
    _curate_job["running"] = False
    _curate_job["finished"] = True
    _curate_job["stopped"] = _curate_cancel.is_set()
    _curate_job["ts"] = int(time.time())


async def _get_head(url: str, limit: int = PROBE_MAX_BYTES) -> tuple[int, str, bytes]:
    """GET only the first `limit` bytes of a response (streaming).
    Keeps curate probes from buffering whole manifests/segments in RAM."""
    async with client.stream("GET", url, timeout=CURATE_TIMEOUT) as r:
        ctype = (r.headers.get("content-type") or "").lower()
        buf = bytearray()
        async for chunk in r.aiter_bytes(4096):
            buf.extend(chunk)
            if len(buf) >= limit:
                break
        return r.status_code, ctype, bytes(buf[:limit])


async def _probe_channel(ch) -> bool:
    """Deep probe: fetch the URL AND verify it's actually playable,
    reading only the first few KB of each response."""
    url = ch["url"]
    try:
        status, ctype, body = await _get_head(url)
        if status not in (200, 206):
            return False
        if ".m3u8" in url.lower() or "mpegurl" in ctype:
            text = body.decode("utf-8", "replace")
            if "#EXTM3U" not in text[:64]:
                return False
            if "#EXT-X-STREAM-INF" in text:
                variant = next((ln.strip() for ln in text.splitlines()
                                if ln.strip() and not ln.startswith("#")), None)
                if not variant:
                    return False
                vstatus, _vct, vbody = await _get_head(urljoin(url, variant))
                if vstatus != 200:
                    return False
                vtext = vbody.decode("utf-8", "replace")
                if "#EXTM3U" not in vtext[:64] or "#EXTINF" not in vtext:
                    return False
            elif "#EXTINF" not in text:
                return False
            return True
        if not body:
            return False
        if ctype.startswith("text/html") and b"<html" in body[:512].lower():
            return False
        return True
    except Exception:
        return False


@app.get("/api/curate/status")
async def curate_status():
    return JSONResponse(_curate_job)


@app.post("/api/curate/stop")
async def curate_stop():
    if _curate_cancel:
        _curate_cancel.set()
    return {"ok": True}


# -------- runtime-reported dead channels (geo-blocked, player-rejected…) ----
# A channel can pass the curate probe (manifest loads, HTTP 200) yet still be
# unplayable — geo-blocks commonly 403 the SEGMENTS, which only shows up at
# play time. The UI reports those here so they join the dead list.
_dead_reported: set[int] = set()


@app.post("/api/dead")
async def report_dead(request: Request):
    body = await request.json()
    num = int(body.get("num", 0))
    if num:
        _dead_reported.add(num)
        _curate_job["dead_nums"] = sorted(set(_curate_job["dead_nums"]) | {num})
    return {"ok": True, "reported_dead": len(_dead_reported)}


@app.get("/api/dead")
async def list_dead():
    return JSONResponse(sorted(_dead_reported))


@app.get("/stream", include_in_schema=False)
async def stream(request: Request, url: str = Query(...)):
    """Proxy a stream so the browser never touches the origin directly.

    HLS manifests are rewritten on the fly: every URI (variant playlists,
    segments, keys) becomes an absolute /stream?url=... reference, so the
    player's follow-up requests also route through the proxy. Without this
    hls.js resolves segment paths against /stream and they 404.
    """
    upstream_headers = {"User-Agent": "G-IPTV/1.0 (+LAN home server)"}
    if "range" in request.headers:
        upstream_headers["Range"] = request.headers["range"]

    # HLS manifests are small text files — read fully and rewrite.
    # Everything else streams through untouched (no buffering).
    looks_hls = ".m3u8" in url.lower()
    if looks_hls:
        r = await client.get(url, headers=upstream_headers, timeout=30)
        ctype = (r.headers.get("content-type") or "").lower()
        if r.status_code in (200, 206) and \
           ("mpegurl" in ctype or r.content.lstrip()[:7] == b"#EXTM3U"):
            text = r.content.decode("utf-8", "replace")
            out_lines = []
            for ln in text.splitlines():
                s = ln.strip()
                if s.startswith("#"):
                    ln = re.sub(
                        r'URI="([^"]+)"',
                        lambda m: 'URI="/stream?url=%s"' %
                                  __import__("urllib.parse").parse.quote(
                                      urljoin(url, m.group(1)), safe=""),
                        ln)
                    out_lines.append(ln)
                elif s:
                    out_lines.append("/stream?url=" + __import__("urllib.parse")
                                     .parse.quote(urljoin(url, s), safe=""))
                else:
                    out_lines.append(ln)
            body = "\n".join(out_lines)
            return PlainTextResponse(
                body,
                status_code=r.status_code,
                media_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        # not really HLS despite the extension — fall through to streaming

    # non-HLS (or failed manifest): stream the body through untouched
    req = client.build_request("GET", url, headers=upstream_headers)
    upstream = await client.send(req, stream=True)

    hop = {"connection", "keep-alive", "transfer-encoding", "content-encoding",
           "content-length", "te", "trailer", "upgrade"}
    out_headers = {k: v for k, v in upstream.headers.items()
                   if k.lower() not in hop}
    out_headers["Access-Control-Allow-Origin"] = "*"

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(65536):
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=upstream.headers.get("content-type"),
    )


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
