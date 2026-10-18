#!/bin/sh
# Graph-Me-Maybe installer for macOS (and Linux, best effort).
#
#   curl -fsSL https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.sh | sh
#
# Run again to update. Options, in the environment (or -y / --uninstall):
#   GRAPHME_VERSION=v0.1.0      a release tag (default: the latest)
#   GRAPHME_INSTALL_SOURCE=DIR  take the release files from a folder or URL instead
#   GRAPHME_HOME=DIR            the app's folder (settings, graph data, web app, viewer)
#   GRAPHME_YES=1               answer yes (installs Docker Desktop when it is missing)
#   GRAPHME_NO_DOCKER=1  GRAPHME_NO_SHORTCUT=1  GRAPHME_NO_LAUNCH=1
#   GRAPHME_UNINSTALL=1         remove the app; GRAPHME_PURGE=1 also its settings and data
#   UV_NO_MODIFY_PATH=1         leave your shell's PATH setup (rc files) alone
set -eu

REPO="ToBeSuperior/Graph-Me-Maybe"
TOOL="graph-me-maybe"

say() { printf 'graph-me-maybe: %s\n' "$*"; }
die() { printf 'graph-me-maybe: %s\n' "$*" >&2; exit 1; }

# Questions go to the terminal: under `curl | sh` standard input is this script.
ask() {
  [ -n "${GRAPHME_YES:-}" ] && return 0
  if [ -n "${GRAPHME_NO_TTY:-}" ] || ! { true </dev/tty; } 2>/dev/null; then return 1; fi
  printf '%s [y/N] ' "$1" >/dev/tty
  read -r answer </dev/tty || return 1
  case $answer in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

app_home() {
  if [ -n "${GRAPHME_HOME:-}" ]; then printf '%s' "$GRAPHME_HOME"; return; fi
  case $(uname -s) in
    Darwin) printf '%s' "$HOME/Library/Application Support/Graph-Me-Maybe" ;;
    *) printf '%s' "${XDG_DATA_HOME:-$HOME/.local/share}/graph-me-maybe" ;;
  esac
}

ensure_uv() {
  command -v uv >/dev/null 2>&1 && return
  say "installing uv (https://docs.astral.sh/uv/)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="${UV_INSTALL_DIR:-$HOME/.local/bin}:$PATH"
  export PATH
  command -v uv >/dev/null 2>&1 || die "uv did not install"
}

release_tag() {
  if [ -n "${GRAPHME_VERSION:-}" ]; then printf '%s' "$GRAPHME_VERSION"; return; fi
  # The API first: right after a release, github.com/.../releases/latest still pointed at the
  # one before for a while. The redirect stays for when the API's hourly limit is used up.
  tag=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1)
  if [ -n "$tag" ]; then printf '%s' "$tag"; return; fi
  url=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest") \
    || die "could not find the latest release"
  printf '%s' "${url##*/}"
}

fetch() {  # fetch NAME DIR
  if [ -d "$SOURCE" ]; then
    [ -f "$SOURCE/$1" ] || die "$1 is not in $SOURCE"
    cp "$SOURCE/$1" "$2/$1"
  else
    curl -fsSL "$SOURCE/$1" -o "$2/$1" || die "could not download $1"
  fi
}

# A Graph-Me-Maybe already answering: updating under it swaps its files while it runs.
app_running() {
  curl -fsS --max-time 2 "http://127.0.0.1:${GRAPHME_PORT:-8765}/health" 2>/dev/null \
    | grep -q '"status" *: *"ok"'
}

open_hint() {
  if [ -n "${GRAPHME_HOME:-}" ]; then printf 'GRAPHME_HOME="%s" graphme app\n' "$HOME_DIR"
  else printf 'graphme app\n'; fi
}

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

verify() {  # verify NAME DIR
  want=$(awk -v name="$1" '$2 == name { print $1 }' "$2/SHA256SUMS")
  [ -n "$want" ] || die "$1 is not in SHA256SUMS"
  [ "$(sha256 "$2/$1")" = "$want" ] || die "$1: checksum does not match SHA256SUMS"
}

has_chrome() {
  case $(uname -s) in
    Darwin) [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] \
      || [ -x "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] ;;
    *) command -v google-chrome >/dev/null 2>&1 || command -v chromium >/dev/null 2>&1 ;;
  esac
}

offer_docker() {
  [ -n "${GRAPHME_NO_DOCKER:-}" ] && return
  if command -v docker >/dev/null 2>&1; then
    docker info >/dev/null 2>&1 || say "Docker is installed but not running: start Docker Desktop before building a graph"
    return
  fi
  say "Docker runs the graph database (Neo4j). Docker Desktop is free for personal use and small"
  say "companies; larger ones need a paid plan. It asks for your password once, the first time it starts."
  if [ "$(uname -s)" = Darwin ] && command -v brew >/dev/null 2>&1 && ask "Install Docker Desktop now with Homebrew?"; then
    brew install --cask docker-desktop && say "open Docker Desktop once and accept its terms" && return
  fi
  say "get Docker Desktop from https://www.docker.com/products/docker-desktop/, start it once, then open the app again"
}

make_shortcut() {  # make_shortcut BIN_DIR
  [ -n "${GRAPHME_NO_SHORTCUT:-}" ] && return
  [ "$(uname -s)" = Darwin ] || return 0
  app="$HOME/Applications/Graph-Me-Maybe.app"
  mkdir -p "$app/Contents/MacOS"
  cat >"$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Graph-Me-Maybe</string>
<key>CFBundleIdentifier</key><string>io.github.tobesuperior.graph-me-maybe</string>
<key>CFBundleExecutable</key><string>Graph-Me-Maybe</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
EOF
  # Finder starts an app with a bare PATH; docker lives in Homebrew's or Docker's own folder.
  printf '#!/bin/sh\nPATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin:$PATH"; export PATH\nGRAPHME_HOME="%s" exec "%s/graphme" app >>"%s/app.log" 2>&1\n' \
    "$HOME_DIR" "$1" "$HOME_DIR" \
    >"$app/Contents/MacOS/Graph-Me-Maybe"
  chmod +x "$app/Contents/MacOS/Graph-Me-Maybe"
  say "shortcut: $app"
}

uninstall() {
  if [ -n "${GRAPHME_PURGE:-}" ]; then
    case $HOME_DIR in
      /*) ;;
      *) die "GRAPHME_PURGE: GRAPHME_HOME must be an absolute path, not '$HOME_DIR'" ;;
    esac
    dir=$(cd "$HOME_DIR" 2>/dev/null && pwd -P || printf '%s' "$HOME_DIR")
    home=$(cd "$HOME" && pwd -P)
    # Neither the root nor any folder holding your home (your home itself included).
    case $dir in /) dir="" ;; esac
    case "$home/" in
      "$dir"/*) die "GRAPHME_PURGE: refusing to delete $HOME_DIR (set GRAPHME_HOME to the app's own folder)" ;;
    esac
  fi
  ensure_uv
  uv tool uninstall "$TOOL" 2>/dev/null || true
  rm -rf "$HOME_DIR/web" "$HOME_DIR/viewer" "$HOME/Applications/Graph-Me-Maybe.app"
  if [ -n "${GRAPHME_PURGE:-}" ]; then
    # Only what the app made; the folder goes only if nothing else is left in it.
    for part in .env .graphme app.log install.ps1; do rm -rf "${HOME_DIR:?}/$part"; done
    if rmdir "$HOME_DIR" 2>/dev/null; then say "removed $HOME_DIR"
    else say "removed the app's files; kept $HOME_DIR, which holds files it did not make"; fi
  fi
  say "removed. The graph database stays in Docker: docker rm -f graphme-neo4j;" \
      "docker volume rm graphme-neo4j-data graphme-neo4j-logs"
}

install() {
  ensure_uv
  tag=$(release_tag)
  SOURCE=${GRAPHME_INSTALL_SOURCE:-"https://github.com/$REPO/releases/download/$tag"}
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  fetch SHA256SUMS "$work"
  wheel=$(awk '$2 ~ /^graph_me_maybe-.*\.whl$/ { print $2; exit }' "$work/SHA256SUMS")
  [ -n "$wheel" ] || die "no graph_me_maybe-*.whl in SHA256SUMS"
  for name in "$wheel" web.zip viewer.zip; do fetch "$name" "$work"; verify "$name" "$work"; done
  if app_running; then
    die "Graph-Me-Maybe is running -- quit it first (pkill -f \"graphme app\"), then run the installer again"
  fi

  say "installing Graph-Me-Maybe $tag"
  uv tool install --force --python 3.11 "$work/$wheel"
  [ -n "${UV_NO_MODIFY_PATH:-}" ] || uv tool update-shell >/dev/null 2>&1 || true
  bin=$(uv tool dir --bin)
  python="$(uv tool dir)/$TOOL/bin/python"

  mkdir -p "$HOME_DIR"
  rm -rf "$HOME_DIR/web" "$HOME_DIR/viewer"
  "$python" -m zipfile -e "$work/web.zip" "$HOME_DIR/web"
  "$python" -m zipfile -e "$work/viewer.zip" "$HOME_DIR/viewer"

  if ! has_chrome; then
    say "no Google Chrome: fetching Playwright's Chromium to draw pages with"
    "$python" -m playwright install chromium --only-shell || say "warning: Playwright's Chromium did not download; install Google Chrome, or run: $python -m playwright install chromium --only-shell"
  fi
  offer_docker
  make_shortcut "$bin"
  say "installed. Open it with: $(open_hint)   (a new terminal finds the command)"
  if [ -z "${GRAPHME_NO_LAUNCH:-}" ]; then
    nohup "$bin/graphme" app >>"$HOME_DIR/app.log" 2>&1 &
    say "starting -- the browser opens in a moment (log: $HOME_DIR/app.log)"
  fi
}

main() {
  for arg in "$@"; do
    case $arg in
      -y|--yes) GRAPHME_YES=1 ;;
      --uninstall) GRAPHME_UNINSTALL=1 ;;
      -h|--help) sed -n '2,13p' "$0" 2>/dev/null; exit 0 ;;
      *) die "unknown option: $arg" ;;
    esac
  done
  [ "$(id -u)" -ne 0 ] || die "run this as yourself, not root: everything goes under your home"
  case $(uname -s) in Darwin|Linux) ;; *) die "on Windows use install.ps1" ;; esac
  HOME_DIR=$(app_home)
  if [ -n "${GRAPHME_UNINSTALL:-}" ]; then uninstall; else install; fi
}

[ -n "${GRAPHME_INSTALL_LIB:-}" ] || main "$@"
