#!/usr/bin/env bash
# Install (symlink) this repo's OpenCode config into ~/.config/opencode.
# Existing real files are backed up with a .bak suffix.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DST="${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}"

mkdir -p "$DST"
for item in AGENTS.md opencode.jsonc agents commands skills; do
  if [ -e "$DST/$item" ] && [ ! -L "$DST/$item" ]; then
    mv "$DST/$item" "$DST/$item.bak"
  fi
  ln -sfn "$SRC/$item" "$DST/$item"
done
echo "Installed OpenCode config from $SRC -> $DST"
