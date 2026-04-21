"""Shared Spotify client + helpers used by both the Slack bot and the web UI."""
import os
import threading
import time
import spotipy
from spotipy.oauth2 import SpotifyOAuth

SCOPE = " ".join([
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
])

_auth = SpotifyOAuth(
    client_id=os.environ["SPOTIFY_CLIENT_ID"],
    client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
    redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
    scope=SCOPE,
    cache_path=".cache",
    open_browser=False,
)
sp = spotipy.Spotify(auth_manager=_auth)

DEVICE_NAME = os.environ.get("SPOTIFY_DEVICE_NAME")


_playback_cache = {"state": None, "ts": 0.0}
_playback_lock = threading.Lock()
PLAYBACK_TTL = 2.5


def cached_playback(force=False):
    """Single source of truth for current_playback(). Caches for ~2.5s so
    frontend polls + queue tick don't duplicate upstream calls."""
    now = time.time()
    with _playback_lock:
        if not force and _playback_cache["state"] is not None and (now - _playback_cache["ts"]) < PLAYBACK_TTL:
            return _playback_cache["state"]
        state = sp.current_playback()
        _playback_cache["state"] = state
        _playback_cache["ts"] = now
        return state


def ensure_device():
    state = cached_playback()
    if state and state.get("device") and state["device"].get("is_active"):
        return state["device"]["id"]
    devices = sp.devices().get("devices", [])
    if not devices:
        return None
    target = None
    if DEVICE_NAME:
        target = next((d for d in devices if d["name"] == DEVICE_NAME), None)
    target = target or devices[0]
    sp.transfer_playback(target["id"], force_play=False)
    return target["id"]


def now_playing():
    state = cached_playback()
    if not state or not state.get("item"):
        return None
    t = state["item"]
    images = t.get("album", {}).get("images", [])
    return {
        "name": t["name"],
        "artist": ", ".join(a["name"] for a in t["artists"]),
        "album": t["album"]["name"],
        "image": images[0]["url"] if images else None,
        "is_playing": state.get("is_playing", False),
        "progress_ms": state.get("progress_ms", 0),
        "duration_ms": t.get("duration_ms", 0),
        "volume": state.get("device", {}).get("volume_percent", 0),
        "shuffle": state.get("shuffle_state", False),
    }
