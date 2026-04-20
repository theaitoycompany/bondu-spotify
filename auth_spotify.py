"""Run once locally to produce .cache with a refresh token for the office account.

Usage:
    python auth_spotify.py

Log in as the OFFICE Spotify account in the browser window that opens.
The resulting .cache file contains the refresh token the bot will use.
"""
import os
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

SCOPE = " ".join([
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
])

auth = SpotifyOAuth(
    client_id=os.environ["SPOTIFY_CLIENT_ID"],
    client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
    redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
    scope=SCOPE,
    open_browser=True,
    cache_path=".cache",
)

token = auth.get_access_token(as_dict=True)
print("OK. Refresh token stored in .cache")
print("Access token preview:", token["access_token"][:20], "...")
