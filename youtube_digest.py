#!/usr/bin/env python3
"""
youtube_digest.py

Pipeline for the "Lindy Later" playlist -> RSS digest automation.
Designed to be called by a Claude Code Routine, which does the
summarizing itself and calls this script for the mechanical parts.

Usage:
    python youtube_digest.py check-new
        Lists videos in the playlist that haven't been published to the
        feed yet, with their transcripts (best-effort). Prints JSON to
        stdout: [{"video_id", "title", "url", "transcript"}]
        The Routine should read this, write a summary per video, then
        call add-item for each one.

    python youtube_digest.py add-item --video-id ID --title "T" --summary "S"
        Adds one <item> to rss.xml and marks the video as published in
        state.json. Idempotent: calling it twice for the same video is
        a no-op the second time.

Environment variables required:
    YOUTUBE_API_KEY      YouTube Data API v3 key (for listing playlist items)
    YOUTUBE_PLAYLIST_ID  The "Lindy Later" playlist ID
    FEED_PUBLIC_URL      Public URL the feed will be served at (for <link>)

Files (relative to cwd -- these live in the git repo the Routine commits):
    state.json  {"published": ["videoId1", "videoId2", ...]}
    rss.xml     the feed itself; created on first run if missing
"""

import os
import json
import argparse
import datetime
import html
import urllib.request
import urllib.parse

STATE_FILE = "state.json"
FEED_FILE = "rss.xml"

FEED_TITLE = "Scott's Video Digest"
FEED_DESC = "Auto-summarized videos from the Lindy Later YouTube playlist."
MAX_ITEMS_IN_FEED = 200


def feed_link():
    return os.environ.get("FEED_PUBLIC_URL", "https://example.github.io/video-digest/rss.xml")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"published": []}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def api_get(url, params):
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"
    with urllib.request.urlopen(full_url) as resp:
        return json.loads(resp.read().decode())


def fetch_playlist_video_ids():
    api_key = os.environ["YOUTUBE_API_KEY"]
    playlist_id = os.environ["YOUTUBE_PLAYLIST_ID"]
    video_ids = []
    page_token = None
    while True:
        params = {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": 50,
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token
        data = api_get("https://www.googleapis.com/youtube/v3/playlistItems", params)
        for item in data.get("items", []):
            snippet = item["snippet"]
            video_ids.append({
                "video_id": snippet["resourceId"]["videoId"],
                "title": snippet["title"],
            })
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return video_ids


def fetch_transcript(video_id):
    """Best-effort transcript fetch. Returns None if unavailable so the
    Routine can fall back to reading the video description or skipping."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None
    try:
        segments = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join(seg["text"] for seg in segments)
    except Exception:
        return None


def cmd_check_new():
    state = load_state()
    published = set(state.get("published", []))
    playlist_items = fetch_playlist_video_ids()
    new_items = []
    for item in playlist_items:
        if item["video_id"] in published:
            continue
        transcript = fetch_transcript(item["video_id"])
        new_items.append({
            "video_id": item["video_id"],
            "title": item["title"],
            "url": f"https://www.youtube.com/watch?v={item['video_id']}",
            "transcript": transcript,
        })
    print(json.dumps(new_items, indent=2))


def load_feed_items():
    """Hand-rolled extraction of existing <item> blocks -- avoids an XML
    parser dependency and preserves formatting of untouched items."""
    if not os.path.exists(FEED_FILE):
        return []
    with open(FEED_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    items = []
    start = 0
    while True:
        i = content.find("<item>", start)
        if i == -1:
            break
        j = content.find("</item>", i)
        items.append(content[i:j + len("</item>")])
        start = j + len("</item>")
    return items


def build_feed(items):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    items_xml = "\n".join(items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>{html.escape(FEED_TITLE)}</title>
<link>{html.escape(feed_link())}</link>
<description>{html.escape(FEED_DESC)}</description>
<lastBuildDate>{now}</lastBuildDate>
{items_xml}
</channel>
</rss>
"""


def cmd_add_item(video_id, title, summary):
    state = load_state()
    published = state.get("published", [])
    if video_id in published:
        print(f"{video_id} already published, skipping.")
        return

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    item_xml = f"""<item>
<title>{html.escape(title)}</title>
<link>{html.escape(video_url)}</link>
<guid isPermaLink="false">{html.escape(video_id)}</guid>
<pubDate>{now}</pubDate>
<description>{html.escape(summary)}</description>
</item>"""

    existing_items = load_feed_items()
    all_items = [item_xml] + existing_items  # newest first
    all_items = all_items[:MAX_ITEMS_IN_FEED]

    with open(FEED_FILE, "w", encoding="utf-8") as f:
        f.write(build_feed(all_items))

    published.append(video_id)
    state["published"] = published
    save_state(state)
    print(f"Added {video_id} to feed and marked as published.")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-new")

    add_p = sub.add_parser("add-item")
    add_p.add_argument("--video-id", required=True)
    add_p.add_argument("--title", required=True)
    add_p.add_argument("--summary", required=True)

    args = parser.parse_args()
    if args.command == "check-new":
        cmd_check_new()
    elif args.command == "add-item":
        cmd_add_item(args.video_id, args.title, args.summary)


if __name__ == "__main__":
    main()
