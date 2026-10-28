# Installation

The one-line installers put the app and everything it needs on your machine: Python (through
[uv](https://docs.astral.sh/uv/)), the web app, the document viewer, a browser to draw pages with,
the sample documents, and the graph database — Neo4j Community and a Java runtime. No Docker, no
administrator rights and no account are needed.

- [Install](#install)
- [What gets installed, and where](#what-gets-installed-and-where)
- [The graph database](#the-graph-database)
- [Update](#update)
- [Uninstall](#uninstall)
- [Windows and WSL](#windows-and-wsl)
- [Installer options](#installer-options)
- [Troubleshooting](#troubleshooting)

## Install

**macOS and Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.sh | sh
```

**Windows** (PowerShell, not WSL)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.ps1 | iex"
```

The installer checks every download against the release's `SHA256SUMS`, opens the app once to make
sure it starts, then launches it. On Windows, if the Microsoft Visual C++ runtime is missing, it says
so and installs it with winget (Windows asks for permission); without winget it gives the download
link and stops.

Afterwards, open **Graph-Me-Maybe** from Applications (macOS) or the Start menu (Windows). It opens in
a window of its own (WebView2 on Windows, WebKit on macOS) and quits when you close the window.
`graphme app` in a terminal opens the same app in a browser tab instead; Linux and WSL always use the
browser.

Put your model key in **Manage › Keys and model**. The key is checked before it is saved (by listing
the provider's models, which costs nothing), saved to the app's `.env`, and shown afterwards only as
"set · …last four characters".

## What gets installed, and where

| | macOS | Windows | Linux |
|---|---|---|---|
| App folder | `~/Library/Application Support/Graph-Me-Maybe` | `%LOCALAPPDATA%\Graph-Me-Maybe` | `${XDG_DATA_HOME:-~/.local/share}/graph-me-maybe` |
| Commands | `graphme`, `graphme-app` (uv tool) | `graphme.exe`, `graphme-app.exe` (uv tool) | `graphme`, `graphme-app` (uv tool) |
| Shortcut | `~/Applications/Graph-Me-Maybe.app` | Start menu | — |

The app folder holds:

| Path | What |
|---|---|
| `.env` | your settings and keys (mode 600) |
| `.graphme/` | the app's working data: uploaded files, crops (unused ones swept away), pages looked at (`server/previews`, 500 MB at most by default) |
| `neo4j/`, `jre/` | Neo4j Community and the Java runtime (replaced on update) |
| `neo4j-data/` | the graph itself (kept on update): the first workspace's |
| `workspaces/`, `workspaces.json` | every other workspace's graph, and which one is open |
| `models/`, `llama/` | the search model and the server that runs it, fetched on first start (about 640 MB for the general model) |
| `web/`, `viewer/` | the web app and the document viewer |
| `webview/` | the app window's storage (theme, language) |
| `app.log` | the window app's log |

The `graphme` command uses this folder's settings, graph and viewer from any directory — unless the
current directory has a `.env` of its own, as a source checkout does.

Without Google Chrome, Playwright's own Chromium is downloaded to draw pages with (no administrator
rights).

## The graph database

The app runs its own Neo4j Community as a child process, listening on 127.0.0.1 only, at the first
free port from 7687. It asks for a password the app makes the first time and keeps in
`neo4j-data/password`, readable by your user alone; the app and `graphme` commands use it
without asking you. It starts with the app (usually within ten seconds; the app shows
"Preparing the graph DB" meanwhile) and stops when the app closes. A `graphme` command uses the app's
Neo4j when the app is open, and otherwise starts one for the command and stops it afterwards.

- If Neo4j and Java were not downloaded at install time, the app fetches them the first time it needs
  the graph (about 210 MB, checksums checked).
- A Neo4j answering on that port is used only if it keeps *this* app folder's data — another install
  on the same machine (a WSL copy, for example) is left alone.
- To use a Neo4j of your own (Neo4j Desktop, a server, Aura), set `GRAPHME_NEO4J_URI` — and
  `GRAPHME_NEO4J_PASSWORD` if it has one — in `.env`. The app then starts nothing.
- `.dump` export and the Aura upload use the bundled `neo4j-admin` on a scratch copy; the app's own
  graph is never touched.

For thousands of documents, give Neo4j more memory. The page cache holds the passages' vectors (about
12 KB per passage), so make it at least their total size. New values apply the next time the app
starts.

```dotenv
GRAPHME_NEO4J_HEAP=4g
GRAPHME_NEO4J_PAGECACHE=8g
```

To empty the graph completely, close the app and delete the `neo4j-data` folder; the next start
begins with an empty graph. A graph made by version 0.1.12 or earlier lives in Docker and is removed
with `docker rm -f graphme-neo4j; docker volume rm graphme-neo4j-data graphme-neo4j-logs`.

## Update

Close the app and run the install line again. If the app is still open the installer changes nothing
and stops; close its window (or, if no window is visible, `pkill -f graphme` on macOS, or end
`graphme-app.exe` in Task Manager on Windows).

## Uninstall

- **macOS / Linux:** `curl -fsSL https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.sh | sh -s -- --uninstall`
- **Windows:** Settings › Apps › Graph-Me-Maybe › Uninstall, or `$env:GRAPHME_UNINSTALL=1` before the
  install line. Close the app first.

An uninstall removes the commands, the shortcut, the web app, the viewer, Neo4j and Java, and keeps
your settings and graph. Add `GRAPHME_PURGE=1` to remove those too: only what the app made is
deleted (`.env`, `.graphme`, `web`, `viewer`, `neo4j-data`, `webview`, `app.log`, and on Windows
`install.ps1`), and the folder itself only if nothing else is left in it. A purge refuses to run when
the app folder is your home folder or contains it.

Left behind: Playwright's Chromium (delete `~/Library/Caches/ms-playwright` on macOS or
`%LOCALAPPDATA%\ms-playwright` on Windows) and uv itself (`uv cache clean`, then
[uv's uninstall steps](https://docs.astral.sh/uv/getting-started/installation/#uninstallation)).

## Windows and WSL

Install on Windows from PowerShell. Running `install.sh` inside WSL prints the PowerShell line and
stops, so you do not end up with two apps; `curl … | GRAPHME_WSL=1 sh` installs inside WSL anyway.

If both are installed, they keep apart: ports a WSL app opens (8765, 7687…) are also reachable on
Windows' 127.0.0.1, but each app starts on the next free port and uses only its own graph.

To remove a copy installed in WSL completely (settings and graph included), run in WSL:

```bash
pkill -f graphme
curl -fsSL https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.sh | GRAPHME_PURGE=1 sh -s -- --uninstall
docker rm -f graphme-neo4j; docker volume rm graphme-neo4j-data graphme-neo4j-logs   # a graph from 0.1.12 or earlier
```

Inside WSL, `graphme app` opens the page in Windows' default browser (`wslview`, else
`explorer.exe`); set `BROWSER` to choose another. Korean and other Hangul text is drawn with the
bundled Pretendard font, so no fonts need installing; for Chinese or Japanese documents install a CJK
font (`sudo apt install fonts-noto-cjk`).

## Installer options

Set these in the environment of the `sh` after the pipe — `GRAPHME_HOME=… curl … | sh` sets it for
`curl` only and has no effect:

```bash
curl -fsSL https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.sh | GRAPHME_HOME=~/gmm sh
```

```powershell
$env:GRAPHME_HOME='D:\gmm'; irm https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.ps1 | iex
```

| Variable | Effect |
|---|---|
| `GRAPHME_VERSION` | install this release tag instead of the latest |
| `GRAPHME_HOME` | the app folder |
| `GRAPHME_NO_NEO4J=1` | do not fetch Neo4j and Java now; the app fetches them when it first needs the graph |
| `GRAPHME_NO_SHORTCUT=1`, `GRAPHME_NO_LAUNCH=1` | no shortcut; do not open the app afterwards |
| `GRAPHME_INSTALL_SOURCE` | take the release files from a folder or another URL |
| `GRAPHME_UNINSTALL=1`, `GRAPHME_PURGE=1` | uninstall; also remove settings and graph |
| `UV_NO_MODIFY_PATH=1` | leave your shell's PATH setup alone |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `the graph DB (Neo4j) did not download` | No internet, or dist.neo4j.org, api.adoptium.net or github.com unreachable. Check the connection and open the app again. |
| `the graph DB (Neo4j) did not start: …` | Neo4j's own words follow the colon; the full logs are in the app folder's `neo4j-data/logs/console.log` and `neo4j.log`. |
| `the Microsoft Visual C++ runtime is missing` (Windows) | Let winget install it, or install the redistributable from the printed link and run the installer again. |
| `OPENAI_API_KEY is required …` | Search is set to OpenAI's embeddings and there is no key yet: put it in **Manage › Keys and model**, or choose a search model run on this machine there. The terminal's `graphme` reads the app folder's `.env`. |
| `the Neo4j at … does not take the password` | `GRAPHME_NEO4J_PASSWORD` in `.env` is wrong. Fix it, or remove `GRAPHME_NEO4J_*` to use the app's own Neo4j. |
| `the environment variables … take precedence over .env` (Keys and model page) | A shell variable of the same name overrides the file: `unset` it and restart. |
| DOCX, PPTX or XLSX pages are not drawn | The viewer bundle is missing (`setup.sh` in a checkout), or Chrome is not where the app looks: set `GRAPHME_CHROME_EXECUTABLE`. |
| "The web app is not built" instead of the app | In a checkout: `(cd frontend && npm ci && npm run build)`. |
| `setup.sh` stops with `wasm-pack: not found` | Install wasm-pack (see the README's requirements) and run it again. |
| `setup.sh` hangs at `building patched xlsx from …` | npm left a registry connection open (seen on WSL). Ctrl+C, then `npm_config_audit=false npm_config_fund=false src/graphme/adapters/rendering/viewer/setup.sh`; packages already fetched come from npm's cache. |
| `rust-lld: Library not loaded: @rpath/libLLVM.dylib` | See the symlink fix in `src/graphme/adapters/rendering/viewer/README.md`. |
