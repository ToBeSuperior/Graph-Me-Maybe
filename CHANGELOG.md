# Changelog

This project follows the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format and uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.20] - 2026-10-20

### Fixed

- A document uploaded or carried in another one opens from its kept pages after a restart,
  instead of being drawn again into a second folder.
- A page drawing that fails is said as such ("the pages could not be drawn"), not as a file that
  may be damaged.
- A text box drawn inside an Excel chart is read as text the chart holds, like one laid over it.

## [0.1.19] - 2026-10-20

### Fixed

- The app answers from the moment it starts: the startup check of the model and embedding
  servers runs behind it instead of before it. The installers compile the app's Python once at
  install, so the first start after an update does not.
- Text boxes on a worksheet are read: one over a chart or a picture goes under it, and its words
  are found by search. Only a box over a picture was kept before, inside the picture's metadata;
  one over a chart or on its own was lost.
- Two drawings that only graze at a corner -- a chart beside a table and a photo below it -- stay
  two pictures. Merged, their rectangle took the table and the headings between them, and the
  sheet's outline put every section under the table.
- A `.dump` export works on Windows. Its scratch Neo4j is stopped by force there, and the dump
  refused the database it left ("Active logical log detected"); the scratch Neo4j now checkpoints
  every write before it is stopped. CI makes a `.dump` on Windows and macOS.
- A file edited and built again replaces what was read from it before: the old reading no longer
  stays beside the new one, where answers wavered between the two values. With the app running,
  an edited file is read again instead of counting as known.
- A file moved to another folder is found where it is now, for its pages and its download.
- A document drawn once opens from its kept pages after a restart without its file: a moved or
  deleted original no longer stops its pages from opening, and opening it no longer reads the
  whole file to check them.
- A document number or code asked on its own (`QP-210`) finds the Korean sentences naming it with
  a particle run on (`QP-210의`), which the search's analyzer had kept as one word.

## [0.1.18] - 2026-10-19

### Changed

- Everything is in English by default: the app opens in English unless Korean is chosen in the
  settings menu (it used to follow the browser's language), server, CLI and installer messages are
  in English, and the sample documents are an English set of the same fictional company. An answer
  still follows the question's language.
- The README is rewritten for new users and contributors, with installation, the user guide and
  retrieval moved to `docs/installation.md`, `docs/user-guide.md` and `docs/retrieval.md`; all
  documentation is in English.
- The contributor kit: a new `CONTRIBUTING.md` with a map of the code, issue forms for bugs and
  feature requests, an English pull request template, `CODE_OF_CONDUCT.md` and `SECURITY.md`.
- A worksheet's outline is asked like a slide's or a page's: text a chart or picture holds goes
  out inside that visual, not again beside it, and pictures and charts are placed by the extent
  they are drawn at rather than the cells they are anchored to. Text and tables are still placed
  by their cells.
- Reading a workbook no longer draws a preview of every table and chart that nothing used, and
  matplotlib is no longer a dependency.
- `import.sh` and the app's own `.dump` build the `neo4j-admin import` command and the graph's
  constraints and indexes from one place, so a new index reaches the export too.
- Reopened documents are redrawn only when the code that draws, reads or binds them changes, or the
  browser does: a release changing only the server, the CLI or the wording no longer redraws every
  document once.

### Fixed

- A page whose model answer is not JSON at all is asked again, like one that misses a field,
  instead of being left unread on the first try.
- Manage › Library keeps asking while the graph DB starts, as the map does, instead of saying it
  cannot reach the graph DB until the page is opened again.
- When the graph DB fails to start, every page waiting for it is told; none starts it again inside
  its own request.
- A downloaded zip's folder and file names hold no control characters or marks that turn text
  around on screen.

## [0.1.17] - 2026-10-19

### Fixed

- On Windows without the Visual C++ runtime, the installer finished with only a warning that the graph DB could not be downloaded. The app then would not start, and the cause was not visible. The installer now loads the app once. If a runtime DLL (`vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll`) is missing, it says so and installs the Visual C++ redistributable with winget (Windows asks for permission). If that fails, it prints the download address and ends the installation as failed. If the DLLs are present and the app still fails, it shows the error Python raised, unchanged.
- `missing-files.txt` listed only document IDs. For a document in the graph, it now lists the folder it was meant to go into, the file name and the path it was looked up at. For an ID that is not in the graph, it says so.

## [0.1.16] - 2026-10-19

### Changed

- When a document is opened after the app is restarted, DOCX, PPTX and XLSX files are no longer drawn again. The renderer stores the geometry it measured (`pages.json`) and the document graph after binding (`bound.json`) next to the page images. It uses them when the file content, the code and the viewer bundle are unchanged and every picture the graph points to is still stored. Reopening runs neither Chrome nor extraction.

## [0.1.15] - 2026-10-19

### Added

- Download the original files of the documents found by a question in one go. Use **Download** above the results to choose files one by one, by hop (1st, 2nd, 3rd), or with select all / clear all, and download them as one zip. Inside the zip, files are split into folders by hop, as on screen. Files with the same name are both included. Files that cannot be opened are listed in `missing-files.txt` (`GET /v1/documents/files.zip`).

## [0.1.14] - 2026-10-19

### Fixed

- On English Windows (console cp1252), the installer's graph DB download failed because of one Korean message line. The message is now written in English, and characters that cannot be printed are replaced.
- The Windows installer only warned when a download failed but still left a failing exit code (the Chromium download behaved the same way).

## [0.1.13] - 2026-10-19

### Changed

- **Docker is no longer used.** On Windows, Docker Desktop ran dozens of CLI plugins every 12 seconds while any container was running, which made the whole PC stall. The app now starts the graph DB (Neo4j Community 5.26) itself.
  - The installer downloads Neo4j and a Java runtime (Temurin 21, about 210MB together) from the official distribution sites, checks the checksums and places them in `neo4j/` and `jre/` in the app folder. If the download failed, the app downloads them when it first opens (`GRAPHME_NO_NEO4J=1` postpones this).
  - When the app opens, Neo4j starts as a child process without a window (no password, 127.0.0.1 only, the first free port from 7687). It stops when the app closes. The terminal `graphme` uses that Neo4j if the app is running. Otherwise it starts one and stops it when it finishes.
  - The graph lives in `neo4j-data/`, so it survives updates. A Neo4j that answers on that port is not used unless it uses the data of this app folder (for example, the WSL app on the same PC).
  - `.dump` and the Aura upload use the `neo4j-admin` that is downloaded with it, on a graph built separately in a temporary folder.
  - Removed: the Docker Desktop install offer, the automatic start and stop, and `GRAPHME_DOCKER_AUTOSTART`, `GRAPHME_NEO4J_ADMIN_IMAGE`, `GRAPHME_NO_DOCKER` and `GRAPHME_YES`.
  - Graphs that were in Docker up to 0.1.12 are not migrated. Read the documents again, and delete the leftover containers with `docker rm -f graphme-neo4j; docker volume rm graphme-neo4j-data graphme-neo4j-logs`.
- If `.env` has `GRAPHME_NEO4J_URI`, the app does not start Neo4j and uses that one (Neo4j Desktop, another server, Aura). `GRAPHME_HOME` in `.env` can change the app folder.
- Uninstall: a normal uninstall removes Neo4j and Java (`neo4j`, `jre`) and keeps the graph (`neo4j-data`). With `GRAPHME_PURGE=1`, the graph is removed too.

### Fixed

- The app window (`graphme-app <folder>`) did not pass the app folder it received as an argument to the server, so the server used the default app folder.

## [0.1.12] - 2026-10-19

### Fixed

- Docker Desktop that the app had started automatically stayed running after the app was closed. On Windows, Docker Desktop can run CLI plugins all at once every few seconds and make the whole PC freeze for moments. Now a Docker Desktop started by the app is stopped when the app window closes (Windows `docker desktop stop`, macOS `quit app "Docker"`). A Docker the user had already started is left alone.

## [0.1.11] - 2026-10-19

### Fixed

- The screen did not update itself.
  - While Docker and the graph DB were getting ready, the screen stopped asking again after three 503 responses and stayed on "Cannot connect". Now, while the server answers "preparing", the screen keeps asking every 3 seconds and shows progress instead of an error. It changes as soon as the server is ready. The error and "Try again" appear only on a real failure (Docker is off, or it is not ready after more than 3 minutes).
  - The document list and the links map are requested again each time you enter the tab and each time you return to the window. A graph changed by `graphme build-graph` in the terminal is also picked up.
- While the Neo4j container was starting after Docker came up, screen requests were blocked for more than 20 seconds. Opening the graph now happens in the background, and a screen request is answered within 2 seconds with "Preparing the graph DB".
- On Korean Windows, creating a `.dump` printed a `UnicodeDecodeError` because docker output was read as cp949. Output of programs the app runs is now read as UTF-8.
- `graphme export-corpus OUT --dump` created a file without an extension. It now adds `.dump`, and if a folder is given it creates `graph.dump` inside it.

## [0.1.10] - 2026-10-19

### Fixed

- The `.dump` export failed on Windows. `import.sh`, `schema.cypher` and `README.md` in the export bundle were saved with CRLF, and the container's `sh` rejected `set -e`. They are now written with LF on every OS.
- The export failure message repeated the same text. It now shows the first line of the error (the Aura password is masked).
- Documents read after the graph was cleared did not appear on the Ask screen or the links map. Clearing removed the build state from the cache, so the screen never saw the end of the next build.
- While the graph DB was opened for the first time (starting Docker, pulling the Neo4j image), screen requests were blocked for minutes. If it does not open within 2 seconds, the server answers "Preparing the graph DB" and the screen asks again by itself. Builds and exports still wait.

### Changed

- `graphme build-graph` now also creates the question embeddings after reading (same as the app). Use `--no-embed` to skip them.
- On macOS, no window is shown when Docker Desktop is started automatically (`open -g -j`).
- In the app window, F5 and Ctrl+R (Cmd+R on macOS) reload the page.
- The document names on the document links map are drawn in a larger font.

## [0.1.9] - 2026-10-18

### Fixed

- A Windows shortcut made before 0.1.5 pointed to `graphme-app.exe` with the `app` argument still attached, so the app folder became `…\Graph-Me-Maybe\app`. The result was the API docs instead of the screen, no keys, graph or viewer, and one level deeper each time it opened. The installer now clears the shortcut arguments, and `graphme-app` accepts only a full path as the app folder.
- Automatic start of Docker Desktop did not work on Windows. `docker desktop start` can report success with "already running" even when it is off, so the app now runs `Docker Desktop.exe` directly.
- Closing the app window could leave the process running because of leftover threads. The app now ends right away when the window closes.

## [0.1.8] - 2026-10-18

### Added

- If the graph is needed and Docker Desktop is off, the app starts it (Windows `docker desktop start`, or `Docker Desktop.exe` if that is missing; macOS `open -a Docker`). While it starts, the screen shows "Starting Docker" and asks again by itself (the server answers 503 with `Retry-After`). If it is still not ready after 3 minutes, the app tells you to check whether a prompt such as a terms agreement is open. Turn this off with `GRAPHME_DOCKER_AUTOSTART=0`.

### Fixed

- When Docker was off, `docker` was run again for every request. The "off" result is now remembered for 5 seconds, so the requests of one screen share one check.
- README: how to completely remove an app installed in WSL, including its keys and graph.

## [0.1.7] - 2026-10-18

### Fixed

- In the Windows app window, a black console window flashed each time Docker, page drawing or export ran. Every program the app runs is now run without a window (in one place, `graphme/processes.py`).

### Changed

- Running `install.sh` in WSL no longer installs. It points to the Windows installation (`install.ps1` in PowerShell). This prevents two copies of the app on one PC. To install in WSL anyway, use `GRAPHME_WSL=1`. Removing an already installed WSL app (`--uninstall`) works as before.

## [0.1.6] - 2026-10-18

### Added

- App window: Graph-Me-Maybe in the Start menu (Windows) and the Applications folder (macOS) now opens in an app window with no address bar, instead of a browser tab and a black console window (new command `graphme-app`, pywebview). The server runs behind the window and stops when the window is closed. If the app window cannot be shown, it opens in the browser. `graphme app` still opens in the browser.

### Fixed

- The terminal `graphme` command read only the `.env` in the current folder, so it could not use the installed app's keys and graph data. If the current folder has no `.env`, it now uses the one in the app folder.
- When a command cannot run (no key, Docker off, and so on), it now prints one line, `오류: …` (Error: …), instead of a long traceback. For a key error, it also says where to enter the key.
- The WSL app and the Windows app on one PC took each other for their own. `/health` now reports the app folder, and the app launcher and the installer count only an app in the same folder as "running". If Docker is off or the app's Neo4j container is off, and another Neo4j answers on that port, the app does not use it and says why.
- If `GRAPHME_NEO4J_PASSWORD` is set, the app does not start a Neo4j container (the user is deliberately using a Neo4j they started themselves).

## [0.1.5] - 2026-10-18

### Removed

- The old path that handled one document as a SQLite index, and its CLI commands `ingest`, `query`, `inspect`, `evaluate` and `export-neo4j`. Documents are now put into the graph space with `build-graph`, queried with `ask` and exported with `export-corpus`. The settings that only that path read, `GRAPHME_HIERARCHY_MODE`, `GRAPHME_UNIT_GROUPING`, `GRAPHME_NODE_BUDGET`, `GRAPHME_TOKEN_BUDGET` and `GRAPHME_MAX_HOPS`, are no longer read either.
- Unused code (a grounding path that nothing called, VLM page splitting, fields that were never read) and duplicate code were cleaned up.

### Fixed

- After installing with the installer, the CLI and Python API (such as `graphme build-graph`) could not find the viewer in the app folder, so they could not draw DOCX, PPTX or XLSX. All commands now use the installed viewer. If there is no viewer, they say so and explain how to build it.
- Excel: when the same cell on two sheets had formulas reading the same table, the second link was missing. The format of a JPEG picture is now written as `image/jpeg`, not `image/jpg`.
- The internal separator in attachment file names (`.emb…`) showed up in the evidence list of the Python API example and the HTTP example (`graphme.shown_name`).
- With colima's default port forwarding (ssh), the first Neo4j started could not be reached. The README and the error message now point to `--port-forwarder grpc`.

### Changed

- Excel and PDF extraction no longer read the same area or page several times (same result, faster).

## [0.1.4] - 2026-10-18

### Fixed

- The installer finds the latest release through the GitHub API. Right after a release, the `releases/latest` address pointed to the previous version for a while, so a version one lower was installed (that address is used only when the API limit is exhausted).
- Linux and WSL: Korean text on DOCX, PPTX and XLSX pages is drawn with the font bundled with the app (Pretendard). Even without a Korean font on the system, it does not break into boxes, and installing `fonts-noto-cjk` is no longer needed.
- If the app was restarted right after being stopped, it came up on 8766 instead of 8765. A connection that has just closed (TIME_WAIT) is no longer treated as the port being in use.

## [0.1.3] - 2026-10-18

### Fixed

- Linux and WSL: if there is no Korean font, the installer tells you to install `fonts-noto-cjk` (0.1.4 replaced this with the app's own font, so the message was dropped).
- README: updated for the Linux and WSL installation path, Node 24, the public repository address, and the installer also installing the web app.

## [0.1.2] - 2026-10-18

### Fixed

- WSL: `graphme app` opens the screen in the Windows default browser, not in Linux Chrome inside WSL (can be changed with `BROWSER`).

## [0.1.1] - 2026-10-18

### Fixed

- Korean text in table and chart previews was drawn as boxes (□). The Korean font Pretendard (SIL OFL) is now included in the package and used for drawing.
- Windows: text files are read and written as UTF-8, so rendering no longer hangs on documents that contain Korean. `xlsx-bundle` failed with "Access is denied" when it tried to change an open file; this is fixed too.

## [0.1.0] - 2026-10-18

### Added

- One-line installers for macOS and Windows (`install.sh`, `install.ps1`) and `graphme app`; pages
  drawn through Playwright for Python (no Node needed to run).
- Python API: `graphme.read` (read one document) and `GraphMe` (add, embed, search, ask, documents, delete, reset, export).
- Model providers: for chat, OpenAI, Anthropic and Gemini (`GRAPHME_PROVIDER`); for embeddings, OpenAI and Gemini
  (`GRAPHME_EMBEDDING_PROVIDER`). Only OpenAI has been checked with real calls.
- HTTP server: `graphme serve` — `/v1` (files, job polling and cancel, search, answer, export), `/health`, `/docs`,
  and locking with `GRAPHME_API_KEY`.
