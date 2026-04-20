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
    try:
        q = sp.queue()
    except Exception:
        return {"items": []}
    items = []
    for t in (q.get("queue") or [])[:20]:
        if not t:
            continue
        images = t.get("album", {}).get("images", [])
        items.append({
            "uri": t["uri"],
            "name": t["name"],
            "artist": ", ".join(a["name"] for a in t["artists"]),
            "image": images[-1]["url"] if images else None,
        })
    return {"items": items}


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


@api.post("/api/play")
def api_play(req: PlayReq):
    device_id = ensure_device()
    if not device_id:
        raise HTTPException(503, "no device available")
    if req.kind == "track":
        sp.start_playback(device_id=device_id, uris=[req.uri])
    elif req.kind == "artist":
        artist_id = req.uri.split(":")[-1]
        top = sp.artist_top_tracks(artist_id)["tracks"]
        sp.start_playback(device_id=device_id, uris=[t["uri"] for t in top])
    else:
        sp.start_playback(device_id=device_id, context_uri=req.uri)
        if req.kind == "playlist":
            sp.shuffle(True, device_id=device_id)
    return {"ok": True}


class QueueReq(BaseModel):
    uri: str


@api.post("/api/queue")
def api_queue(req: QueueReq):
    device_id = ensure_device()
    if not device_id:
        raise HTTPException(503, "no device available")
    sp.add_to_queue(req.uri, device_id=device_id)
    return {"ok": True}


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
