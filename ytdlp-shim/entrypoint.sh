#!/bin/sh
# yt-dlp's YouTube extractor breaks whenever YouTube changes their player, sometimes for
# hours until upstream ships a fix. Re-installing the latest release on every container start
# (Watchtower recreates this container on its own schedule, same as everything else on this
# stack) is the cheap way to stay current without a manual rebuild-and-redeploy cycle each time.
# --no-deps: fastapi/uvicorn are already pinned correctly, this should only ever touch yt-dlp.
# --user: this container runs as a non-root user (see Dockerfile) with no write access to the
# image's own site-packages, installed at build time as root. --user writes into this user's
# own home directory instead, which Python picks up automatically via user site-packages, and
# shadows the older build-time copy without needing root at runtime.
set -e
pip install --quiet --user --upgrade --no-deps yt-dlp || echo "yt-dlp self-update failed, continuing with the currently installed version"
exec uvicorn app.main:app --host 0.0.0.0 --port 8090
