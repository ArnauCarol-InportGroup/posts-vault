#!/usr/bin/env python3
"""Fill the Buffer queue with the next posts from the encrypted vault.

Each post holds one .mp4 (published) and a post.md (the post text). The script
tops the queue up to the plan limit, taking posts in folder-name order and
wrapping back to the first one after the last.

Buffer needs a public URL for the video, so each queued video is decrypted and
uploaded under a random name to the "media" GitHub release, and deleted again
once Buffer has published (or dropped) the post.

Usage:
    python tools/buffer_fill.py            # create posts
    python tools/buffer_fill.py --dry-run  # show what would be created

Environment:
    BUFFER_KEY       Buffer API key
    POSTS_PASSWORD   vault password
    BUFFER_CHANNEL   optional channel id; defaults to the only LinkedIn channel
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import vault

API_URL = "https://api.buffer.com"
RELEASE_TAG = "media"
IN_CI = bool(os.environ.get("GITHUB_ACTIONS"))


class BufferError(Exception):
    pass


def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ['BUFFER_KEY']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"Buffer API HTTP {e.code}: {e.read().decode(errors='replace')}")
    if payload.get("errors"):
        raise BufferError(payload["errors"])
    return payload["data"]


def label(index, folder_name):
    # Actions logs of a public repo are public: never print post names there.
    return f"post #{index + 1}" if IN_CI else folder_name


# ------------------------------------------------------------- media host


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()


def repo_name():
    return os.environ.get("GITHUB_REPOSITORY") or gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")


def ensure_release():
    try:
        gh("release", "view", RELEASE_TAG)
    except subprocess.CalledProcessError:
        gh("release", "create", RELEASE_TAG, "--title", RELEASE_TAG, "--notes", "", "--latest=false")


def upload_video(data):
    asset = secrets.token_hex(16) + ".mp4"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / asset
        path.write_bytes(data)
        gh("release", "upload", RELEASE_TAG, str(path))
    return asset, f"https://github.com/{repo_name()}/releases/download/{RELEASE_TAG}/{asset}"


def delete_asset(asset):
    try:
        gh("release", "delete-asset", RELEASE_TAG, asset, "--yes")
    except subprocess.CalledProcessError as e:
        if "not found" not in (e.stderr or "").lower():
            raise


def is_public(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.status == 200
    except urllib.error.URLError:
        return False


# ----------------------------------------------------------------- buffer


def pick_channel(org_id):
    channels = gql(
        """query($org: OrganizationId!) {
             channels(input: {organizationId: $org}) {
               id name displayName service isDisconnected isQueuePaused
             }
           }""",
        {"org": org_id},
    )["channels"]
    wanted = os.environ.get("BUFFER_CHANNEL")
    if wanted:
        matches = [c for c in channels if c["id"] == wanted]
    else:
        matches = [c for c in channels if c["service"] == "linkedin"]
    if len(matches) != 1:
        sys.exit(f"Expected exactly one channel, found {len(matches)}. Set BUFFER_CHANNEL.")
    channel = matches[0]
    if channel["isDisconnected"]:
        sys.exit("The Buffer channel is disconnected; reconnect it first.")
    if channel["isQueuePaused"]:
        print("WARNING: the Buffer queue is paused; posts will wait until it is resumed.")
    return channel


def count_scheduled(org_id):
    count, after = 0, None
    while True:
        page = gql(
            """query($org: OrganizationId!, $after: String) {
                 posts(first: 100, after: $after,
                       input: {organizationId: $org, filter: {status: [scheduled]}}) {
                   edges { node { id } }
                   pageInfo { hasNextPage endCursor }
                 }
               }""",
            {"org": org_id, "after": after},
        )["posts"]
        count += len(page["edges"])
        if not page["pageInfo"]["hasNextPage"]:
            return count
        after = page["pageInfo"]["endCursor"]


def post_status(post_id):
    try:
        return gql("query($id: PostId!) { post(input: {id: $id}) { status } }", {"id": post_id})["post"]["status"]
    except BufferError as e:
        if any(err.get("extensions", {}).get("code") == "NOT_FOUND" for err in e.args[0]):
            return "deleted"
        raise


def create_post(channel_id, text, url):
    try:
        result = gql(
            """mutation($input: CreatePostInput!) {
                 createPost(input: $input) {
                   __typename
                   ... on PostActionSuccess { post { id dueAt } }
                   ... on MutationError { message }
                 }
               }""",
            {
                "input": {
                    "channelId": channel_id,
                    "text": text,
                    "schedulingType": "automatic",
                    "mode": "addToQueue",
                    "assets": [{"video": {"url": url}}],
                }
            },
        )["createPost"]
    except BufferError as e:
        raise BufferError(f"createPost failed: {e}") from e
    if result["__typename"] != "PostActionSuccess":
        raise BufferError(f"createPost failed: {result.get('message', result)}")
    return result["post"]


# ------------------------------------------------------------------- main


def cleanup_published(pw, state):
    """Delete the hosted video of every post Buffer no longer needs."""
    removed = 0
    for entry in state.get("history", []):
        if not entry.get("asset"):
            continue
        if post_status(entry["post_id"]) in ("sent", "deleted"):
            delete_asset(entry["asset"])
            entry["asset"] = None
            vault.save_state(pw, state)
            removed += 1
    if removed:
        print(f"Removed {removed} hosted video(s) of published posts.")


def next_index(names, next_name):
    """Index of next_name, or of the first post after it (if it was removed)."""
    if next_name in names:
        return names.index(next_name)
    later = [i for i, n in enumerate(names) if n > (next_name or "")]
    return later[0] if later else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="show what would be created, change nothing")
    args = parser.parse_args()

    pw = vault.password()
    if not os.environ.get("BUFFER_KEY"):
        sys.exit("BUFFER_KEY is not set (add it to .env or the environment).")

    posts = vault.load_posts(pw)
    if not posts:
        sys.exit("The vault is empty.")
    names = [name for name, _ in posts]
    state = vault.load_state(pw)

    if not args.dry_run:
        cleanup_published(pw, state)

    org = gql("{ account { organizations { id limits { scheduledPosts } } } }")["account"]["organizations"][0]
    channel = pick_channel(org["id"])
    limit = org["limits"]["scheduledPosts"]
    scheduled = count_scheduled(org["id"])
    free = max(0, limit - scheduled)
    print(f"Queue: {scheduled}/{limit} scheduled -> {free} free slot(s)")

    idx = next_index(names, state.get("next"))
    if free and not args.dry_run:
        ensure_release()

    for _ in range(free):
        folder_name, files = posts[idx]
        videos = sorted(f for f in files if f.lower().endswith(".mp4"))
        if not videos or "post.md" not in files:
            sys.exit(f"{label(idx, folder_name)} has no video or post.md.")
        text = files["post.md"].decode("utf-8").strip()

        if args.dry_run:
            print(f"[dry-run] would queue {label(idx, folder_name)}")
        else:
            asset, url = upload_video(files[videos[0]])
            try:
                if not is_public(url):
                    raise BufferError("hosted video is not publicly reachable")
                post = create_post(channel["id"], text, url)
            except BufferError as e:
                delete_asset(asset)
                sys.exit(f"{label(idx, folder_name)}: {e}")
            print(f"Queued {label(idx, folder_name)} -> {post['dueAt']}")
            state.setdefault("history", []).append({
                "folder": folder_name,
                "post_id": post["id"],
                "due_at": post["dueAt"],
                "asset": asset,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

        idx = (idx + 1) % len(posts)
        if not args.dry_run:
            # Save after every post so a failure halfway never re-queues a post.
            state["next"] = names[idx]
            vault.save_state(pw, state)

    print(f"Next: {label(idx, names[idx])}")
    if not IN_CI and not args.dry_run and free:
        print("Recuerda subir vault/state.bin a GitHub (Sincronizar posts.bat).")


if __name__ == "__main__":
    main()
