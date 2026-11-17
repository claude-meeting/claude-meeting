#!/bin/bash
# Copy (or update) the engine bundled in the Mac app into Application Support, keeping the user's
# Python environment, models and state. Called by the setup guide; lives next to the bundled engine.
#   sync-engine.sh <destination engine folder> <app version>
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:?destination}"
VERSION="${2:?version}"

mkdir -p "$DEST/app" "$DEST/bin"
# Code and prebuilt helpers: replaced whole. asr-venv, models and state stay as they are.
for d in bin lib src tools; do
  rm -rf "$DEST/app/$d"
  cp -R "$SRC/app/$d" "$DEST/app/$d"
done
cp -p "$SRC/app/config.example.toml" "$SRC/app/requirements.txt" "$DEST/app/"
cp -p "$SRC/install.sh" "$SRC/uninstall.sh" "$SRC/sync-engine.sh" "$DEST/"
# The bundled uv is read-only (Homebrew installs it 0555) and cp -p keeps that, so the copy from the
# last sync can't be overwritten: remove it first.
rm -f "$DEST/bin/uv"
cp -p "$SRC/bin/uv" "$DEST/bin/uv"
# Files copied out of a downloaded app carry the quarantine flag; the helpers would be refused.
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
# .version is written by the app once install.sh succeeded: a half-done update must still count as
# outdated, or the new code would run against the old Python environment.
rm -f "$DEST/.version"
echo "engine $VERSION in $DEST"
