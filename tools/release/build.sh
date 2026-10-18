#!/bin/sh
# Build the release the installers take: the wheel, the web app, the viewer bundle and their
# checksums.   tools/release/build.sh OUT_DIR
# Needs uv, Node/npm, and what the viewer's setup.sh needs (rustup + wasm32, wasm-pack).
set -eu
[ $# -eq 1 ] || { echo "usage: tools/release/build.sh OUT_DIR" >&2; exit 2; }
mkdir -p "$1"
out=$(cd "$1" && pwd)
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
rm -f "$out"/*.whl "$out/web.zip" "$out/viewer.zip" "$out/SHA256SUMS"

uv build --wheel --out-dir "$out"
(cd frontend && npm ci && npm run build)
(cd frontend/dist && zip -qr "$out/web.zip" .)

viewer=$(mktemp -d)
trap 'rm -rf "$viewer"' EXIT
src/graphme/adapters/rendering/viewer/setup.sh "$viewer"
cp THIRD_PARTY_NOTICES.md "$viewer/"
(cd "$viewer" && zip -qr "$out/viewer.zip" dist xlsx THIRD_PARTY_NOTICES.md)

cd "$out"
if command -v sha256sum >/dev/null 2>&1; then sum="sha256sum"; else sum="shasum -a 256"; fi
$sum ./*.whl web.zip viewer.zip | sed 's#  \./#  #' > SHA256SUMS
cat SHA256SUMS
