#!/usr/bin/env bash
# Boots the real Cadence backend, in demo mode, serving the freshly-built web client from
# backend/static -- exactly how the production Docker image wires the two together. Used by
# Playwright's webServer config so end-to-end tests exercise the actual production topology,
# not a dev-server proxy.
set -euo pipefail

WEB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$WEB_DIR/.." && pwd)"
# Normally web/ and backend/ are siblings in the same checkout. CADENCE_BACKEND_DIR overrides
# this for setups where the two live in different working copies (e.g. local dev against an
# out-of-tree venv on a filesystem that can't host one directly).
BACKEND_DIR="${CADENCE_BACKEND_DIR:-$REPO_ROOT/backend}"
STATIC_DIR="$BACKEND_DIR/static"
DATA_DIR="${CADENCE_E2E_DATA_DIR:-$(mktemp -d)}"
PYTHON_BIN="${CADENCE_PYTHON_BIN:-python3}"
PORT="${CADENCE_E2E_PORT:-8199}"

npm run build --prefix "$WEB_DIR"

rm -rf "$STATIC_DIR"
mkdir -p "$STATIC_DIR"
cp -r "$WEB_DIR"/dist/. "$STATIC_DIR"/

export DEMO_MODE=true
export DATA_DIR="$DATA_DIR"
# The suite signs in once per test from a single address, so the production default of
# 10/minute starts returning 429 partway through and fails whichever tests happen to run last.
# Raised only for this throwaway harness; the default is unchanged everywhere else.
export AUTH_RATE_LIMIT="1000/minute"

exec "$PYTHON_BIN" -m uvicorn app.main:app --app-dir "$BACKEND_DIR" --host 127.0.0.1 --port "$PORT"
