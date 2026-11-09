"""
G-IPTV server — FastAPI.

Serves the UI and proxies the playlist, streams, and guide data so that
everything the browser touches is same-origin (no CORS, no mixed content).

Run:  python server/main.py
Then: http://<this-machine-ip>:8484 from any LAN device.
"""

import asyncio
import gzip
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

app = FastAPI(title="G-IPTV")

# ---------------------------------------------------------------- state
_playlist_raw: str = ""
_playlist_fetched: float = 0.0
_channels: list[dict] = []
_epg_fetched: float = 0.0
# tvg-id -> list of (start_epoch, stop_epoch, title)
_epg: dict[str, list[tuple[int, int, str]]] = {}

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
    for i, ch in enumerate(out, 1):
        ch["num"] = i
    return out


_XMLTV_DATE = re.compile(r"(\d{14})")


def _xmltv_epoch(s: str) -> int:
    m = _XMLTV_DATE.search(s or "")
    if not m:
        return 0
    return int(time.mktime(time.strptime(m.group(1), "%Y%m%d%H%M%S")))


async def refresh_playlist(force: bool = False) -> None:
    global _playlist_raw, _playlist_fetched, _channels
    if not force and time.time() - _playlist_fetched < PLAYLIST_CACHE_TTL and _channels:
        return
    try:
        r = await client.get(PLAYLIST_URL, timeout=30)
        r.raise_for_status()
        _playlist_raw = r.text
        _channels = parse_m3u(_playlist_raw)
        _playlist_fetched = time.time()
    except Exception as e:
        # never take the server down over a transient upstream failure;
        # keep serving the previous snapshot if we have one
        print(f"[playlist] refresh failed: {e}")
        if not _channels:
            raise            # only fatal on cold start with no cache


async def refresh_epg(force: bool = False) -> None:
    """Load an XMLTV guide into {tvg_id: [(start, stop, title), ...]}."""
    global _epg, _epg_fetched
    if not EPG_URL:
        return
    if not force and time.time() - _epg_fetched < EPG_CACHE_TTL and _epg:
        return
    try:
        if EPG_URL.startswith(("http://", "https://")):
            r = await client.get(EPG_URL, timeout=60)
            r.raise_for_status()
            raw = r.content
        else:
            raw = Path(EPG_URL).read_bytes()
        if EPG_URL.endswith(".gz"):
            raw = gzip.decompress(raw)

        epg: dict[str, list[tuple[int, int, str]]] = {}
        # iterparse keeps memory sane on big guide files
        for _event, prog in ET.iterparse(
                __import__("io").BytesIO(raw), events=("end",)):
            if prog.tag != "programme":
                continue
            cid = prog.get("channel", "")
            start = _xmltv_epoch(prog.get("start", ""))
            stop = _xmltv_epoch(prog.get("stop", ""))
            title_el = prog.find("title")
            title = (title_el.text or "").strip() if title_el is not None else ""
            if cid and title:
                epg.setdefault(cid, []).append((start, stop, title))
            prog.clear()
        for lst in epg.values():
            lst.sort()
        _epg = epg
        _epg_fetched = time.time()
    except Exception as e:                       # never take the server down
        print(f"[epg] refresh failed: {e}")


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


@app.get("/api/guide")
async def api_guide():
    """Now/next program per channel tvg-id, if an EPG source is configured."""
    await refresh_epg()
    now = int(time.time())
    out: dict[str, dict] = {}
    for cid, progs in _epg.items():
        now_title = next_title = None
        for start, stop, title in progs:
            if start <= now < stop:
                now_title = title
            elif start > now and next_title is None:
                next_title = title
                break
        if now_title or next_title:
            out[cid] = {"now": now_title, "next": next_title}
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
async def curate_start(category: str = Query("ALL"), res: str = Query("__ALL__")):
    global _curate_task, _curate_cancel
    if _curate_job["running"]:
        return JSONResponse({"ok": False, "msg": "already running"}, status_code=409)
    await refresh_playlist()

    targets = _channels
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
