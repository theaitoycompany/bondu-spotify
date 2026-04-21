"""Web UI + Slack bot in one process. Serves a Bondu-themed control panel."""
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

from spotify_client import sp, ensure_device, now_playing  # noqa: E402
from queue_manager import queue_manager  # noqa: E402

SLACK_ENABLED = bool(os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_APP_TOKEN"))
if SLACK_ENABLED:
    from app import start_slack  # noqa: E402

api = FastAPI()
STATIC = Path(__file__).parent / "static"


@api.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@api.get("/api/now")
def api_now():
    return now_playing() or {}


@api.get("/api/queue")
def api_queue_list():
    return {"items": queue_manager.list()}


class QueueMoveReq(BaseModel):
    src: int
    dst: int


@api.post("/api/queue/move")
def api_queue_move(req: QueueMoveReq):
    queue_manager.move(req.src, req.dst)
    return {"ok": True}


@api.post("/api/queue/clear")
def api_queue_clear():
    queue_manager.clear()
    return {"ok": True}


@api.post("/api/queue/shuffle")
def api_queue_shuffle():
    queue_manager.shuffle()
    return {"ok": True}


class QueueIndexReq(BaseModel):
    index: int


@api.post("/api/queue/remove")
def api_queue_remove(req: QueueIndexReq):
    queue_manager.remove(req.index)
    return {"ok": True}


@api.post("/api/queue/skipto")
def api_queue_skipto(req: QueueIndexReq):
    """Jump to the item at index: drop everything before it, play it now."""
    target = queue_manager.skip_to(req.index)
    if not target:
        raise HTTPException(400, "index out of range")
    device_id = ensure_device()
    if not device_id:
        raise HTTPException(503, "no device available")
    sp.start_playback(device_id=device_id, uris=[target["uri"]])
    return {"ok": True, "track": target}


class ResolveReq(BaseModel):
    url: str


def _parse_spotify_url(s: str):
    s = s.strip()
    if s.startswith("spotify:"):
        parts = s.split(":")
        if len(parts) >= 3:
            return parts[1], parts[2], s
    if "open.spotify.com/" in s:
        tail = s.split("open.spotify.com/")[1]
        if tail.startswith("intl-"):
            tail = tail.split("/", 1)[1]
        kind = tail.split("/")[0]
        sid = tail.split("/")[1].split("?")[0].split("#")[0]
        return kind, sid, f"spotify:{kind}:{sid}"
    return None, None, None


@api.post("/api/resolve")
def api_resolve(req: ResolveReq):
    kind, sid, uri = _parse_spotify_url(req.url)
    if not kind:
        raise HTTPException(400, "not a spotify url")
    if kind == "track":
        t = sp.track(sid)
        return {"kind": "track", "uri": uri, "name": t["name"],
                "subtitle": ", ".join(a["name"] for a in t["artists"]),
                "image": (t["album"]["images"][0]["url"] if t["album"]["images"] else None)}
    if kind == "album":
        a = sp.album(sid)
        return {"kind": "album", "uri": uri, "name": a["name"],
                "subtitle": ", ".join(ar["name"] for ar in a["artists"]),
                "image": (a["images"][0]["url"] if a["images"] else None)}
    if kind == "playlist":
        p = sp.playlist(sid, fields="name,owner(display_name),images")
        return {"kind": "playlist", "uri": uri, "name": p["name"],
                "subtitle": p["owner"]["display_name"],
                "image": (p["images"][0]["url"] if p["images"] else None)}
    if kind == "artist":
        ar = sp.artist(sid)
        return {"kind": "artist", "uri": uri, "name": ar["name"],
                "subtitle": "Artist",
                "image": (ar["images"][0]["url"] if ar["images"] else None)}
    raise HTTPException(400, f"unsupported kind: {kind}")


class SearchReq(BaseModel):
    q: str
    kind: str = "track"  # track | album | playlist | artist


@api.post("/api/search")
def api_search(req: SearchReq):
    if req.kind not in ("track", "album", "playlist", "artist"):
        raise HTTPException(400, "bad kind")
    results = sp.search(q=req.q, type=req.kind, limit=10)
    items = [it for it in results[req.kind + "s"]["items"] if it]
    out = []
    for it in items:
        if req.kind == "track":
            out.append({
                "uri": it["uri"], "name": it["name"],
                "subtitle": ", ".join(a["name"] for a in it["artists"]),
                "image": (it["album"]["images"][0]["url"] if it["album"]["images"] else None),
            })
        elif req.kind == "album":
            out.append({
                "uri": it["uri"], "name": it["name"],
                "subtitle": ", ".join(a["name"] for a in it["artists"]),
                "image": (it["images"][0]["url"] if it["images"] else None),
            })
        elif req.kind == "playlist":
            out.append({
                "uri": it["uri"], "name": it["name"],
                "subtitle": it["owner"]["display_name"],
                "image": (it["images"][0]["url"] if it["images"] else None),
            })
        else:
            out.append({
                "uri": it["uri"], "id": it["id"], "name": it["name"],
                "subtitle": "Artist",
                "image": (it["images"][0]["url"] if it["images"] else None),
            })
    return {"items": out}


class PlayReq(BaseModel):
    uri: str
    kind: str = "track"


def _collect_uris(kind: str, uri: str):
    sid = uri.split(":")[-1]
    if kind == "album":
        items = sp.album_tracks(sid, limit=50)["items"]
        return [t["uri"] for t in items if t]
    if kind == "playlist":
        uris = []
        offset = 0
        while True:
            page = sp.playlist_items(sid, limit=100, offset=offset, fields="items(track(uri)),next")
            for item in page.get("items", []):
                tr = item.get("track")
                if tr and tr.get("uri"):
                    uris.append(tr["uri"])
            if not page.get("next"):
                break
            offset += 100
            if offset > 1000:
                break
        return uris
    if kind == "artist":
        top = sp.artist_top_tracks(sid)["tracks"]
        return [t["uri"] for t in top]
    return [uri]


@api.post("/api/play")
def api_play(req: PlayReq):
    device_id = ensure_device()
    if not device_id:
        raise HTTPException(503, "no device available")
    if req.kind == "track":
        sp.start_playback(device_id=device_id, uris=[req.uri])
        return {"ok": True}

    uris = _collect_uris(req.kind, req.uri)
    if not uris:
        raise HTTPException(404, "no tracks found")

    # Clear existing virtual queue, play first track, queue the rest in our manager
    queue_manager.clear()
    sp.start_playback(device_id=device_id, uris=[uris[0]])
    if len(uris) > 1:
        queue_manager.add_uris_bulk(uris[1:])
    return {"ok": True, "queued": max(0, len(uris) - 1)}


class QueueReq(BaseModel):
    uri: str


@api.post("/api/queue")
def api_queue(req: QueueReq):
    track = queue_manager.add(req.uri)
    return {"ok": True, "track": track}


@api.post("/api/pause")
def api_pause():
    sp.pause_playback()
    return {"ok": True}


@api.post("/api/resume")
def api_resume():
    device_id = ensure_device()
    sp.start_playback(device_id=device_id)
    return {"ok": True}


@api.post("/api/skip")
def api_skip():
    sp.next_track()
    return {"ok": True}


@api.post("/api/prev")
def api_prev():
    sp.previous_track()
    return {"ok": True}


class VolReq(BaseModel):
    volume: int


@api.post("/api/volume")
def api_volume(req: VolReq):
    v = max(0, min(100, int(req.volume)))
    sp.volume(v)
    return {"ok": True, "volume": v}


class ShuffleReq(BaseModel):
    state: bool


@api.post("/api/shuffle")
def api_shuffle(req: ShuffleReq):
    sp.shuffle(req.state)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    if SLACK_ENABLED:
        threading.Thread(target=start_slack, daemon=True).start()
    else:
        print("Slack disabled (no SLACK_BOT_TOKEN / SLACK_APP_TOKEN). Web UI only.")
    uvicorn.run(api, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
