#!/usr/bin/env bash
# Build edge-sim if needed, then start the phone console.
# Used by `npm run web` and by the Replit Run button (see /.replit).
set -euo pipefail

cd "$(dirname "$0")/.."

BIN=native/build/edge-sim
needs_build=0
if [ ! -x "$BIN" ]; then
  needs_build=1
else
  for src in native/src/*; do
    if [ "$src" -nt "$BIN" ]; then needs_build=1; fi
  done
fi

if [ "$needs_build" = 1 ]; then
  mkdir -p native/build
  CXX_BIN="${CXX:-}"
  if [ -z "$CXX_BIN" ]; then
    for candidate in g++ c++ clang++; do
      if command -v "$candidate" >/dev/null 2>&1; then CXX_BIN="$candidate"; break; fi
    done
  fi
  if [ -z "$CXX_BIN" ]; then
    echo "No C++ compiler found (tried g++, c++, clang++). Install one and run again." >&2
    exit 1
  fi
  echo "Building edge-sim with $CXX_BIN..."
  "$CXX_BIN" -std=c++17 -O2 -Inative/src native/src/*.cpp -o "$BIN"
fi

exec node web/server.mjs
