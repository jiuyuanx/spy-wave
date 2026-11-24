#!/usr/bin/env python3
"""Publish public/ to gh-pages via the Git Data API (2026-11-24 latency fix).

Replaces peaceiris/actions-gh-pages, which cloned the entire gh-pages branch
(725+ day pages, ~2 GB) on every cycle just to update 3-4 files: deploy step
alone took ~135 s of the ~4.5 min run. This script instead:
  head commit -> base tree -> upload only the blobs in public/ -> new tree
  -> new commit -> fast-forward the ref. History files are never downloaded
  and never touched (same semantics as keep_files: true).

Env: GITHUB_TOKEN, GITHUB_REPOSITORY (both provided by Actions).
"""
import base64
import hashlib
import json
import os
import sys
import urllib.request

API = "https://api.github.com"
REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
PUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")


def call(method, path, payload=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode() or "{}")


def main():
    files = sorted(f for f in os.listdir(PUB) if os.path.isfile(os.path.join(PUB, f)))
    if not files:
        print("public/ empty, nothing to publish")
        return
    if len(files) > 10:
        print("REFUSE: public/ has %d files" % len(files))
        sys.exit(1)

    ref = call("GET", f"/repos/{REPO}/git/ref/heads/gh-pages")
    head_sha = ref["object"]["sha"]
    head = call("GET", f"/repos/{REPO}/git/commits/{head_sha}")
    base_tree = head["tree"]["sha"]

    # upload blobs, dedupe identical contents (index.html == TODAY.html)
    blob_by_hash = {}
    entries = []
    for name in files:
        data = open(os.path.join(PUB, name), "rb").read()
        h = hashlib.sha256(data).hexdigest()
        if h not in blob_by_hash:
            blob = call("POST", f"/repos/{REPO}/git/blobs",
                        {"content": base64.b64encode(data).decode(), "encoding": "base64"})
            blob_by_hash[h] = blob["sha"]
            print("blob %s -> %s (%d bytes)" % (name, blob["sha"][:8], len(data)))
        entries.append({"path": name, "mode": "100644", "type": "blob",
                        "sha": blob_by_hash[h]})

    tree = call("POST", f"/repos/{REPO}/git/trees",
                {"base_tree": base_tree, "tree": entries})
    commit = call("POST", f"/repos/{REPO}/git/commits",
                  {"message": "wave dashboard update (%s)" % ", ".join(files),
                   "tree": tree["sha"], "parents": [head_sha]})
    call("PATCH", f"/repos/{REPO}/git/refs/heads/gh-pages",
         {"sha": commit["sha"], "force": False})
    print("gh-pages -> %s" % commit["sha"][:8])


if __name__ == "__main__":
    main()
