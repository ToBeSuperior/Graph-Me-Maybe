#!/usr/bin/env bash
# Build a graph over HTTP and ask it. Start the server first, in another terminal:
#
#   uv run graphme serve
#   examples/http_ask.sh <folder> "<question>"
#
# The folder is read on the server's machine. GRAPHME_URL is the server (default
# http://127.0.0.1:8765); GRAPHME_API_KEY, when the server has one, is sent as a Bearer token.
set -euo pipefail

usage() {
  echo "usage: examples/http_ask.sh <folder> <question>"
  echo "  start 'graphme serve' first; GRAPHME_URL (default http://127.0.0.1:8765), GRAPHME_API_KEY"
}
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then usage; exit 0; fi
if [[ $# -ne 2 ]]; then usage >&2; exit 2; fi

url=${GRAPHME_URL:-http://127.0.0.1:8765}
api() { curl -fsS ${GRAPHME_API_KEY:+-H "Authorization: Bearer $GRAPHME_API_KEY"} "$@"; }
field() { python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
json() { python3 -c 'import json, sys; print(json.dumps({sys.argv[1]: sys.argv[2]}))' "$1" "$2"; }

folder=$(cd "$1" && pwd)
job=$(api -H 'content-type: application/json' -d "$(json folder "$folder")" "$url/v1/jobs/build" | field id)
while :; do
  state=$(api "$url/v1/jobs/$job" | field state)
  echo "build $job: $state"
  case $state in done) break ;; error|cancelled) api "$url/v1/jobs/$job"; echo; exit 1 ;; esac
  sleep 2
done
api -H 'content-type: application/json' -d "$(json question "$2")" "$url/v1/ask" | python3 -c '
import json, re, sys
said = json.load(sys.stdin)
print(said["answer"])
for source in said["sources"]:  # an attachment without the digest that tells it apart
    print("-", re.sub(r"\.emb[0-9a-f]{8}(?=\.[A-Za-z0-9]{1,8}\b)", "", source["file"]),
          "(%.2f)" % source["score"])'
