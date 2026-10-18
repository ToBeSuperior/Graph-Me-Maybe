#!/usr/bin/env bash
# Build the OOXML viewer bundle the browser renderer serves.
#
# DOCX and PPTX come from the published npm build, which ships the compiled WebAssembly
# parsers; building those locally needs a Rust toolchain that produces wasm32 objects,
# and nothing about them needs changing. XLSX is built from a pinned upstream checkout
# with our patches (one also resolves a combo chart's second bottom category axis), the first
# of which forwards a shape's rotation and flips (upstream parses them but
# drops them before rendering) and exports the grid geometry the crop step maps cells
# through. Compiling that patch needs a Rust toolchain with the wasm32 target.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while IFS='=' read -r key value; do
  case "$key" in
    upstream|commit|npm_package|npm_version) printf -v "$key" '%s' "$value" ;;
  esac
done < "$here/UPSTREAM"
# Where the app looks for it: the argument, else GRAPHME_VIEWER_DIR from the environment or the
# repository's .env, else the cache.
env_file="$here/../../../../../.env"
if [[ -z "${GRAPHME_VIEWER_DIR:-}" && -f "$env_file" ]]; then
  GRAPHME_VIEWER_DIR="$(sed -n 's/^[[:space:]]*GRAPHME_VIEWER_DIR=//p' "$env_file" | tail -1 | tr -d "\"'")"
  GRAPHME_VIEWER_DIR="${GRAPHME_VIEWER_DIR/#\~/$HOME}"
fi
bundle="${1:-${GRAPHME_VIEWER_DIR:-$HOME/.cache/graphme/ooxml-viewer}}"
# Homebrew's rustc has no wasm32 target and cannot be given one: rustup's toolchain goes first
# wherever it is installed (rustup.rs, or Homebrew's rustup).
for rustup_bin in "$HOME/.cargo/bin" "$(brew --prefix rustup 2>/dev/null)/bin"; do
  if [[ -x "$rustup_bin/rustup" ]]; then PATH="$rustup_bin:$PATH"; break; fi
done
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "bundle -> $bundle"
mkdir -p "$bundle/dist" "$bundle/xlsx"

echo "fetching $npm_package@$npm_version (docx, pptx)"
( cd "$work" && npm pack "$npm_package@$npm_version" --silent >/dev/null && tar xzf ./*.tgz )
# The published build is used as shipped except where it breaks on real documents; each
# fix is a patch against that exact version, so moving the version re-surfaces it.
for patch in "$here"/dist-patches/*.patch; do
  [[ -e "$patch" ]] || continue  # an unmatched glob stays literal
  ( cd "$work/package" && patch --quiet -p1 < "$patch" )
done
cp -R "$work/package/dist/." "$bundle/dist/"

if ! rustup target list --installed 2>/dev/null | grep -q wasm32-unknown-unknown; then
  echo "error: the patched xlsx parser needs the wasm32 target" >&2
  echo "  rustup target add wasm32-unknown-unknown" >&2
  exit 1
fi

echo "building patched xlsx from $commit"
git clone --quiet "$upstream" "$work/src"
git -C "$work/src" checkout --quiet --force "$commit"
for patch in "$here"/patches/*.patch; do
  [[ -e "$patch" ]] || continue
  git -C "$work/src" apply "$patch"
done
( cd "$work/src" && npm install --silent )
( cd "$work/src/packages/xlsx" && npm run wasm && npm run build --silent )
cp -R "$work/src/packages/xlsx/dist/." "$bundle/xlsx/"

echo
echo "viewer bundle ready: $bundle"
echo "  dist/   docx + pptx (npm $npm_version)"
echo "  xlsx/   patched build of $commit"
echo
# Only a bundle placed by argument is somewhere the app would not look.
if [[ -n "${1:-}" && "$bundle" != "${GRAPHME_VIEWER_DIR:-$HOME/.cache/graphme/ooxml-viewer}" ]]; then
  echo "point the renderer at it with GRAPHME_VIEWER_DIR=$bundle"
fi
