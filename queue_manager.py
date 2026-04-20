"""Virtual queue layered on top of Spotify.

We maintain our own queue in memory (persisted to disk). A background thread
watches playback; when the current track is nearly over, we push the next
virtual-queue item into Spotify's queue so playback stays gapless.

This gives us full control: clear, reorder, remove, reshuffle — things the
raw Spotify API doesn't allow.
"""
import json
import random
import threading
import time
from pathlib import Path
from typing import Optional

from spotify_client import sp, ensure_device

STATE_FILE = Path(__file__).parent / "queue.json"
HAND_OFF_MS = 8000  # push next track when current has this much left
POLL_SEC = 3


class QueueManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.items: list[dict] = []
        self._last_handoff_uri: Optional[str] = None
        self._load()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ---- persistence ----
    def _load(self):
        if STATE_FILE.exists():
            try:
                self.items = json.loads(STATE_FILE.read_text()).get("items", [])
            except Exception:
                self.items = []

    def _save(self):
        STATE_FILE.write_text(json.dumps({"items": self.items}))

    # ---- public API ----
    def list(self) -> list[dict]:
        with self.lock:
            return list(self.items)

    def add(self, uri: str) -> dict:
        track = self._fetch_track(uri)
        with self.lock:
            self.items.append(track)
            self._save()
        return track

    def add_tracks(self, tracks: list[dict]):
        """Add pre-formatted track dicts (skip per-track API lookups)."""
        with self.lock:
            self.items.extend(tracks)
            self._save()

    def add_uris_bulk(self, uris: list[str]):
        """Fetch metadata in batches of 50 and append. Much faster than add() in a loop."""
        fetched = []
        for i in range(0, len(uris), 50):
            chunk_ids = [u.split(":")[-1] for u in uris[i:i+50]]
            res = sp.tracks(chunk_ids)
            for t in res.get("tracks", []):
                if not t:
                    continue
                images = t.get("album", {}).get("images", [])
                fetched.append({
                    "uri": t["uri"],
                    "name": t["name"],
                    "artist": ", ".join(a["name"] for a in t["artists"]),
                    "image": images[-1]["url"] if images else None,
                    "duration_ms": t.get("duration_ms", 0),
                })
        self.add_tracks(fetched)
        return fetched

    def remove(self, index: int):
        with self.lock:
            if 0 <= index < len(self.items):
                self.items.pop(index)
                self._save()

    def clear(self):
        with self.lock:
            self.items.clear()
            self._save()

    def move(self, src: int, dst: int):
        with self.lock:
            if not (0 <= src < len(self.items)) or not (0 <= dst < len(self.items)):
                return
            item = self.items.pop(src)
            self.items.insert(dst, item)
            self._save()

    def shuffle(self):
        with self.lock:
            random.shuffle(self.items)
            self._save()

    def skip_to(self, index: int) -> Optional[dict]:
        """Pop the item at `index` (dropping everything before it) and return it.
        Caller should start playback with the returned track."""
        with self.lock:
            if not (0 <= index < len(self.items)):
                return None
            target = self.items[index]
            self.items = self.items[index + 1:]
            self._save()
            self._last_handoff_uri = None
            return target

    # ---- helpers ----
    def _fetch_track(self, uri: str) -> dict:
        track_id = uri.split(":")[-1]
        t = sp.track(track_id)
        images = t.get("album", {}).get("images", [])
        return {
            "uri": t["uri"],
            "name": t["name"],
            "artist": ", ".join(a["name"] for a in t["artists"]),
            "image": images[-1]["url"] if images else None,
            "duration_ms": t.get("duration_ms", 0),
        }

    # ---- background loop ----
    def _run(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                print("queue tick error:", e)
            time.sleep(POLL_SEC)

    def _tick(self):
        with self.lock:
            if not self.items:
                return

        state = sp.current_playback()
        if not state or not state.get("item"):
            return

        current_uri = state["item"]["uri"]
        remaining = state["item"]["duration_ms"] - state.get("progress_ms", 0)
        if remaining > HAND_OFF_MS:
            return
        if self._last_handoff_uri == current_uri:
            return

        with self.lock:
            if not self.items:
                return
            next_item = self.items[0]

        device_id = ensure_device()
        if not device_id:
            return
        try:
            sp.add_to_queue(next_item["uri"], device_id=device_id)
            self._last_handoff_uri = current_uri
            with self.lock:
                # Only pop if the current top is still the one we handed off
                if self.items and self.items[0]["uri"] == next_item["uri"]:
                    self.items.pop(0)
                    self._save()
        except Exception as e:
            print("handoff failed:", e)


queue_manager = QueueManager()
