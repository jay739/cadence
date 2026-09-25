#!/usr/bin/env bash
# Regenerate backend/constraints.txt, the exact versions CI, the release workflow and the
# Docker image install (pip install -c constraints.txt ...). pyproject.toml keeps the
# allowed ranges; this file pins what was actually tested.
#
#   scripts/lock-backend.sh                       re-resolve, keeping existing pins where valid
#   scripts/lock-backend.sh --upgrade-package X   move one package to its newest allowed version
#   scripts/lock-backend.sh --upgrade             move everything (review the diff!)
#
# Runs in python:3.12-slim so the result matches CI and the image, whatever the host has.
# CI fails if constraints.txt is out of sync with pyproject.toml.
set -euo pipefail
cd "$(dirname "$0")/../backend"
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e CUSTOM_COMPILE_COMMAND=scripts/lock-backend.sh \
  -v "$PWD:/backend" -w /backend python:3.12-slim sh -c \
  "pip install -q --disable-pip-version-check --no-warn-script-location 'pip-tools>=7.4' >/dev/null && \
   python -m piptools compile -q --extra dev --strip-extras --output-file constraints.txt $* pyproject.toml"
echo "backend/constraints.txt updated; commit it together with pyproject.toml"
