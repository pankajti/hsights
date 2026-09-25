#!/usr/bin/env bash
# Write a lock file of the current environment with no local paths in it.
#
# Why not plain `pip freeze`: in an editable install it emits the project as
#   -e /Users/you/dev/git/hsights
#   hsights @ file:///Users/you/dev/git/hsights
# and in a conda environment it emits locally built wheels as
#   pandas @ file:///opt/anaconda3/conda-bld/pandas_.../work
# All three are absolute paths to your machine and all three fail on Render.
#
# `pip list --format=freeze` is used instead because it always prints
# name==version and never a file:// URL, so conda-built packages survive as
# real pinned requirements rather than being silently dropped.
#
#   ./scripts/freeze.sh                     -> requirements-lock.txt
#   ./scripts/freeze.sh requirements.txt    -> overwrite the runtime list
#
# Keep requirements.txt curated and hand-written: it is the minimum the web
# service needs. Use requirements-lock.txt to reproduce an exact environment.
set -euo pipefail

OUTPUT="${1:-requirements-lock.txt}"

# The project itself and packaging tools the build image already provides.
EXCLUDE=(hsights pip setuptools wheel pkg-resources distribute)
ARGS=()
for name in "${EXCLUDE[@]}"; do ARGS+=(--exclude "$name"); done

python -m pip list --format=freeze "${ARGS[@]}" \
  | grep -v ' @ ' \
  | grep -v '^-e ' \
  | sort -f \
  > "$OUTPUT"

# gunicorn runs the app but is never imported by it, so a development
# environment often lacks it and the freeze silently omits it. The Render
# start command then fails with "gunicorn: command not found".
if ! grep -qiE '^gunicorn[=<>~]' "$OUTPUT"; then
  echo "gunicorn>=22,<24" >> "$OUTPUT"
  echo "note: gunicorn was not installed locally; pinned it in $OUTPUT anyway." >&2
fi

echo "wrote $OUTPUT ($(wc -l < "$OUTPUT" | tr -d ' ') packages)"

if grep -qE 'file://|^-e |/Users/|/home/|[A-Za-z]:\\' "$OUTPUT"; then
  echo "ERROR: a local path survived filtering — inspect $OUTPUT before committing." >&2
  exit 1
fi
