import os
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

from spotify_client import sp, ensure_device  # noqa: E402

app = App(token=os.environ["SLACK_BOT_TOKEN"])


def reply(respond, text):
    respond({"response_type": "in_channel", "text": text})


@app.command("/play")
def cmd_play(ack, respond, command):
    ack()
    query = (command.get("text") or "").strip()
    device_id = ensure_device()
    if not device_id:
        return reply(respond, "No Spotify device found. Open Spotify on the office speaker.")
    if not query:
        sp.start_playback(device_id=device_id)
        return reply(respond, ":arrow_forward: Resumed")
    results = sp.search(q=query, type="track", limit=1)
    items = results["tracks"]["items"]
    if not items:
        return reply(respond, f"No results for *{query}*")
    track = items[0]
    sp.start_playback(device_id=device_id, uris=[track["uri"]])
    reply(respond, f":musical_note: Now playing *{track['name']}* — {track['artists'][0]['name']}")


@app.command("/queue")
def cmd_queue(ack, respond, command):
    ack()
    query = (command.get("text") or "").strip()
    if not query:
        return reply(respond, "Usage: `/queue <song name>`")
    device_id = ensure_device()
    if not device_id:
        return reply(respond, "No Spotify device found.")
    results = sp.search(q=query, type="track", limit=1)
    items = results["tracks"]["items"]
    if not items:
        return reply(respond, f"No results for *{query}*")
    track = items[0]
    sp.add_to_queue(track["uri"], device_id=device_id)
    reply(respond, f":heavy_plus_sign: Queued *{track['name']}* — {track['artists'][0]['name']}")


@app.command("/skip")
def cmd_skip(ack, respond):
    ack()
    sp.next_track()
    reply(respond, ":fast_forward: Skipped")


@app.command("/pause")
def cmd_pause(ack, respond):
    ack()
    sp.pause_playback()
    reply(respond, ":pause_button: Paused")


@app.command("/nowplaying")
def cmd_nowplaying(ack, respond):
    ack()
    state = sp.current_playback()
    if not state or not state.get("item"):
        return reply(respond, "Nothing playing")
    t = state["item"]
    reply(respond, f":notes: *{t['name']}* — {t['artists'][0]['name']} ({t['album']['name']})")


@app.command("/playlist")
def cmd_playlist(ack, respond, command):
    ack()
    query = (command.get("text") or "").strip()
    if not query:
        return reply(respond, "Usage: `/playlist <name or spotify url>`")
    device_id = ensure_device()
    if not device_id:
        return reply(respond, "No Spotify device found.")
    if "spotify.com/playlist/" in query or query.startswith("spotify:playlist:"):
        uri = query
        if "spotify.com/playlist/" in query:
            pid = query.split("playlist/")[1].split("?")[0]
            uri = f"spotify:playlist:{pid}"
        sp.start_playback(device_id=device_id, context_uri=uri)
        sp.shuffle(True, device_id=device_id)
        return reply(respond, ":notes: Playing playlist (shuffled)")
    results = sp.search(q=query, type="playlist", limit=1)
    items = results["playlists"]["items"]
    if not items:
        return reply(respond, f"No playlist found for *{query}*")
    pl = items[0]
    sp.start_playback(device_id=device_id, context_uri=pl["uri"])
    sp.shuffle(True, device_id=device_id)
    reply(respond, f":notes: Playing *{pl['name']}* by {pl['owner']['display_name']} (shuffled)")


@app.command("/album")
def cmd_album(ack, respond, command):
    ack()
    query = (command.get("text") or "").strip()
    if not query:
        return reply(respond, "Usage: `/album <name>`")
    device_id = ensure_device()
    if not device_id:
        return reply(respond, "No Spotify device found.")
    results = sp.search(q=query, type="album", limit=1)
    items = results["albums"]["items"]
    if not items:
        return reply(respond, f"No album found for *{query}*")
    album = items[0]
    sp.start_playback(device_id=device_id, context_uri=album["uri"])
    reply(respond, f":cd: Playing album *{album['name']}* — {album['artists'][0]['name']}")


@app.command("/artist")
def cmd_artist(ack, respond, command):
    ack()
    query = (command.get("text") or "").strip()
    if not query:
        return reply(respond, "Usage: `/artist <name>`")
    device_id = ensure_device()
    if not device_id:
        return reply(respond, "No Spotify device found.")
    results = sp.search(q=query, type="artist", limit=1)
    items = results["artists"]["items"]
    if not items:
        return reply(respond, f"No artist found for *{query}*")
    artist = items[0]
    top = sp.artist_top_tracks(artist["id"])["tracks"]
    if not top:
        return reply(respond, f"No top tracks for *{artist['name']}*")
    sp.start_playback(device_id=device_id, uris=[t["uri"] for t in top])
    reply(respond, f":microphone: Playing top tracks by *{artist['name']}*")


@app.command("/shuffle")
def cmd_shuffle(ack, respond, command):
    ack()
    text = (command.get("text") or "").strip().lower()
    state = text not in ("off", "false", "0")
    sp.shuffle(state)
    reply(respond, f":twisted_rightwards_arrows: Shuffle {'on' if state else 'off'}")


@app.command("/vol")
def cmd_vol(ack, respond, command):
    ack()
    try:
        v = int((command.get("text") or "").strip())
        assert 0 <= v <= 100
    except (ValueError, AssertionError):
        return reply(respond, "Usage: `/vol 0-100`")
    sp.volume(v)
    reply(respond, f":loud_sound: Volume {v}")


def start_slack():
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    start_slack()
