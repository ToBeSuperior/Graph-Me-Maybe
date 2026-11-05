# Graph-Me-Maybe — What Is Left to Do

> Updated: 2026-11-04
>
> Finished work is not listed here. Confirmed behavior is in
> [`../finalized_source.md`](../finalized_source.md), and the history is in
> [`method.md`](method.md).

## Current state in one line

Extraction → browser render → unit crop → VLM hierarchy → graph space → question runs
end to end for worksheets, slides, DOCX and PDF, in an installed app (macOS, Windows) with its
own Neo4j, search run on this machine, workspaces, and folders read from Box, Google Drive and
OneDrive/SharePoint through their desktop apps.
It keeps running behind a menu bar or tray icon, and MCP clients (Claude Code, Claude Desktop,
Cursor) search, read and open its documents. Measured against six other systems on
MMLongBench-Doc and DocBench, published on the site.

## 0 · Next plan

- [ ] (On hold until someone needs it) **Per-feature APIs.** What there is now is kept as it is:
      `graphme.read` (one file read whole, no graph), `GraphMe` (`add`, `embed`, `search`, `ask`,
      `documents`, `delete`, `reset`, `export`), the `/v1` HTTP routes and the CLI. Not open on
      their own: extraction without a model call, putting an `ExtractedDocument` already read
      into the space, cutting passages, linking documents again. Open one when a real use asks
      for it -- say, taking the readings or passages into another store -- rather than all of
      them ahead of time.
- [ ] **A library to `pip install`.** `graphme.read` and `GraphMe` (add, embed, search, ask,
      documents, delete, export) exist, but only inside the app's package. In order:
      1. `py.typed`; the window and the server as extras (`graph-me-maybe[app]`, `[server]`) so the
         core installs alone; `graphme.setup()` fetching the viewer, Neo4j, Java and the search
         model (and doing so on first use when missing -- today only the installer fetches the
         viewer); settings passed in code (`GraphMe(provider=..., api_key=..., neo4j_uri=...)`);
         published on PyPI as `graph-me-maybe` (the name is free; the owner decides the account).
      2. `GraphMe(workspace="...")`; the public API is what `graphme` exports, versioned by semver
         with deprecations announced.
      3. The per-feature APIs of the item above.
      4. An API reference and example notebooks.
- [ ] **A team workspace.** A workspace kept in a folder on a shared drive (Box, Google Drive,
      OneDrive/SharePoint), so a team opens the same graph. That folder is left out of what a
      build reads, whatever folder above it is chosen. To decide first: a running Neo4j's store
      and transaction logs cannot sit in a folder a sync client rewrites, and two machines cannot
      open one graph at once (Community is a single writer) -- so the shared folder may hold a
      snapshot (`.dump` or import bundle) that one member publishes and others open read-only,
      with the live graph staying local.
- [ ] **Mail services as a source.** Connect Gmail, Outlook/Microsoft 365 and other mail
      (IMAP), so a mailbox or a label/folder is read like a folder of documents: each message,
      with its sender, recipients and date, and its attachments read as the documents they are,
      linked to the message that carried them. To decide first: how a mailbox is signed in to
      (each provider's OAuth, or the mail already on this machine); which messages are taken
      (a label, a folder, a date range) and how new mail is picked up after the first build;
      and that mail, like every other source, stays on this machine.
- [ ] **A blog for the public site** (left for later; site plan in
      `docs/superpowers/specs/2026-10-29-public-site-design.md`). Candidates: why plain RAG
      breaks on spreadsheets and slides; how it works, from a rendered page to the graph; the
      benchmark -- the right cell, not just the right document; a vision model reading the outline
      instead of parsing rules; weighted-RRF hybrid search and its numbers; Neo4j inside a desktop
      app without Docker; the data flow, what stays and what goes to the model. A fixed cadence
      (about four a month) once it starts.
- [ ] **A native installer and app.** Today one-line scripts install uv and the app as a Python
      package (Neo4j, Java, the viewer and a browser fetched for it), and the Mac `.app` is a
      two-line shell script calling that command: the process is `python`, unsigned, so macOS
      may ask again for a drive's folder after an update, or name `python` when it asks. Bundle Python, the libraries
      and the app into one `Graph-Me-Maybe.app` / `.exe` (BeeWare Briefcase or PyInstaller) and
      ship a `.dmg` and an `.msi`/setup `.exe`, built by the release workflow for each platform;
      Neo4j, the Java runtime and the search model stay fetched on first start. Unsigned, macOS
      warns of an unidentified developer and Windows SmartScreen warns too, and a company PC may
      refuse it: signing needs an Apple Developer ID (with notarization) and a Windows code-signing
      certificate. To decide: signing, the platforms (Apple Silicon, Intel, Windows), and whether
      the one-line scripts stay.

## 1 · Wire the hierarchy into production indexing

- [x] (Closed 2026-11-05, kept as a record) Record the places where readings differ in the
      graph. Not taken: nothing reads such a record yet (no app mark, no analysis), so which
      reading was chosen and why stays in the log. Add it when the work to reduce build-to-build
      differences needs it.
- [ ] Check an Aura upload end to end (the `.dump` is checked by loading it into an empty Neo4j
      5.26, and the `neo4j-admin database upload` options were checked against 5.26's own
      help). What is left needs an Aura instance and its password: the user's to run, from the
      app's export. Top-level units need no `OUTLINE_PARENT`: the published `PARENT_OF`
      (`final_edges`) already gives every node one parent (`test_published_tree.py`).

## 1.5 · Search and the web app

- [x] (Closed 2026-11-05, kept as a record) Draw a preview in the app's own window with the same
      viewer instead of on the server. Not taken: the app window is WebKit on macOS and an
      Edge (Chromium) WebView on Windows, so drawing there would need each platform matched to the
      boxes the server's Chromium measures. Pages stay drawn by the server's one Chromium on every
      platform; turning pages was made fast by drawing the pages around one ahead.
- [x] (Closed 2026-11-05, kept as a record) Some pages keep watermarks and repeated header
      elements without `drop`. Checked on a public benchmark build: short text repeated on many
      pages of one document is partly furniture (page numbers, a running header) and partly
      content (each chart's source line and notes), so neither a rule nor a "repeats on N pages"
      hint was added. What is left stays with the outline pass's `drop`.
- [ ] Check OneDrive/SharePoint on a machine with OneDrive signed in and a SharePoint library
      synced: the folders it finds (`OneDrive-*`, `OneDrive-SharedLibraries-*`; on Windows the
      registry's mount points) and a build from them. Only invented folders have checked it.
- [ ] Windows: giving a file back to the cloud after it is read is OneDrive's alone (`attrib +U
      -P`); Google Drive and Box Drive there keep what is read in their own capped caches. Not
      yet run on Windows at all.
- [ ] Run each cloud drive end to end with a real account, not invented folders: Box (the desktop
      folder and the API picker, built and previewed), Google Drive for desktop and OneDrive, each
      with files kept online only -- read, given back, previewed again, downloaded.
- [ ] (Later) ONLYOFFICE as a source. It has no desktop client that syncs files to a folder, so
      it cannot be read like Box Drive, Google Drive or OneDrive, and DocSpace has no WebDAV
      server yet. Read DocSpace through its REST API with a personal access token (its address
      and key in Keys and model; rooms and folders browsed in the app, picked files fetched while
      read). A room kept on an outside storage (OneDrive, Box, Google Drive) is read through that
      drive's tab already; Workspace's WebDAV can be mounted and added as a folder.

## 1.6 · The background app and MCP on Windows

- [ ] Check on a Windows PC what has run on macOS only since 0.1.57: the tray icon and its menu
      (open the app, MCP on/off, start at login -- the `Run` value -- and quit completely); the
      notice when the window is first closed (a message box of its own); opening the app again
      from the Start menu after the window was closed; the installer ending the running app
      through `server.json` (`taskkill /T`) before it installs; and `open_document` opening a
      file in Excel or Word.

## 2 · Breaking down tables

- [ ] Decide whether to split a table into cell, row and column semantic units, and if so,
      set the rules. Today one table is one node. This item is on hold until the
      representative table meaning and the ownership rules are settled.
- [ ] Find tables drawn without rules. The PDF table finder follows drawn lines, so a table set
      in whitespace alone is read as loose text. Candidate: a small layout model run on this
      machine (as the search model is), which also tells a table from a page's frame.
- [ ] Decide whether the page-reading model should also check tables covering less than half a
      page when a cell holds a paragraph: a boxed menu or a labelled photo strip still passes the
      frame rule there.
## 3 · Quality evaluation set

- [ ] Build a small representative set with the expected source mapping, information units,
      relations and evidence. `tests/public` already pins each format's rules on invented
      files; what is missing is a set whose whole expected graph is written down.
- [ ] Add a golden evaluation that measures mapping and layout fidelity, split and relation
      quality, evidence recall, citation resolution and answer quality **separately**.
- [ ] Write down the limits and the non-claims together. Do not use confidential material
      without data-handling approval.

## 4 · Live provider smoke test — approval required

- [ ] Run the public package's providers (OpenAI, Anthropic, Gemini) live once each, on
      newly made synthetic, non-confidential documents, after explicit approval -- it is paid.
      Planned as Task 14 of `docs/superpowers/plans/2026-10-16-model-providers.md` and not run:
      confirm the Gemini model id and the Gemini embedder's limits and relevance floor there.
- [ ] Record the sanitized results — readiness, structure counts, citation resolution,
      provider and model identifiers — never secrets or raw provider content, and do not mix
      an embedding-only success into claims about VLM reading or question quality.

## 5 · Extending browser automation

Nothing open. The e2e checks (`frontend/e2e`) cover asking, citations, the folders, builds and
their failures, a stopped search, a wrong key, the drives and workspaces, a folder picked or
dropped and built, the graph still starting (503, Retry-After), and `preflight()` warnings on
the settings page.

## 6 · Reconciling documents

Diagrams are no longer maintained. The pipeline is covered by the stage table in
`finalized_source.md` and the final code list, and keeping a second copy of the same content
as a picture let it drift while nobody was looking — a picture you cannot trust is worse than
no picture. If one is needed, draw a fresh one then.

## 7 · Remaining technical debt

- [ ] Decoding metafiles (EMF, WMF) relies on viewer patches (16-bit and `BI_BITFIELDS` DIBs).
      A JPEG or PNG inside a DIB (`BI_JPEG`, `BI_PNG`) is still not drawn: GDI accepts them only
      on a printer, and the player would need an asynchronous decode first. Add it if one shows
      up in a real document -- a whole picture vanishes without an error.
- [ ] The worker's copy of the metafile player is still unpatched. If rendering moves to the
      worker, the same branch must go in there too (noted in `viewer/README.md`).
- [ ] (Refinement) A unit the figure pass wrongly calls part of a figure is never shown to the
      outline pass again, on any format: its place comes from `figure_part_decisions`
      alone. Worksheets now follow the same rule as slides and pages
      (`build_placed_scope`, pictures and charts placed by their DrawingML extent, text and
      tables by their cells). If a misjudged part turns up, the correction belongs in the
      figure pass or in binding, not in a format-specific exception.
- [ ] Move the viewer's pinned versions forward once upstream merges
      yukiyokotani/office-open-xml-viewer#1983 (smoothed-line axis) and #1984 (combo on a
      hidden secondary category axis): `UPSTREAM`'s `commit` and `npm_version`. Then drop
      `chart_axes.py`, `picture_anchors.py` (fixed upstream in `d7f60b865`) and dist patch
      `0003`, and check every other patch still applies and is still needed.

## 8 · Refinements (low priority)

Kept for later; nothing here is scheduled.

- [ ] Measure on a real corpus how the outline (hierarchy edges) changes search results.
      Every build reads the outline off the page now; what it buys in answers is not measured.
- [ ] Decide the readings per page (`GRAPHME_HIERARCHY_READINGS`, 1; the app 2) from per-page
      call cost and latency -- with 2 readings the calls double, and only pages where the
      readings differ cost one more.
- [ ] Reduce how much one build of a document differs from the next. The same document read
      twice comes out with different table text, captions and block cuts, which moves question
      accuracy by a few points between builds. Candidate: take a table's words from the PDF's
      own text instead of the page-reading model, and keep the model to the structure.
- [ ] Measure what asking for documents costs and buys against asking for passages, on a real
      corpus and a question set with the expected documents. Build the graph in a home of its
      own (`GRAPHME_HOME`, the Neo4j install copied) so no other graph is touched, then ask each
      question both ways (5 passages; 5 documents) and compare: passages gathered, documents
      found, answer tokens, documents the 12,000-token budget leaves unread (efficiency);
      whether the expected document is found and read, and answers graded O/△/X (effect);
      search, link walk and answer time (speed). The build and the answers are model calls:
      decide first whether to grade answers or stop at the search measures.
- [ ] Tune RRF `k` (60), the weight (0.6) and each search model's relevance floor (0.39 general,
      0.46 Korean, 0.2 OpenAI) on a real question set. Today they are set from literature values
      and the invented samples only.
- [ ] Check that lossy WebP crops do not cost picture questions. After crops moved to WebP, one
      benchmark's picture questions came out lower on two answer runs over the same build; try
      the same build with lossless or higher-quality photographs before deciding.
- [ ] Shorten the time to answer one question. Every question makes two model calls (the split
      and the answer); a question that names no document and asks one thing may not need the
      split.
- [ ] Check the small-print rule (difference from the modal body size, presence of body text
      above and below) and the number of pieces per call on more document types. The rule was
      tuned on only two kinds, papers and Korean reports: brochures, PDF slides, financial
      reports, manuals with sidebars and dense pages (contents, references) are unchecked.

## Operating constraints

- Do not call a provider without approval.
- Do not print, copy or commit `.env`, provider API keys or their metadata.
- A fake in the test profile is not live verification.
- When only documents change, run only the markdown and link checks and `git diff --check`.
  Run Ruff, mypy and the tests only when source is changed with them.
