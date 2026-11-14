# Graph-Me-Maybe — What Is Left to Do

> Updated: 2026-11-05
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
      Design for steps 1, 2 and 4 written (`docs/superpowers/specs/2026-11-05-pip-library-design.md`:
      reading only, with no Neo4j or Java, and the whole pipeline). Done 2026-11-14: settings as
      keywords (`graphme.read(path, provider=..., api_key=...)`, `GraphMe(...)`, `CODE_SETTINGS`),
      `py.typed`, a test keeping `import graphme` free of the server, window and MCP packages, and
      `graphme.passages(doc)` (`Passage`: the space's own cut of a document read alone, with
      `file`, `format`, `headings`, `sheet`/`cells`, `slide`, `pages`). Left: the extras split
      and the distribution renamed `graph-me-maybe` (the installers and the release workflow
      install the wheel by name: change them together and run an install), fetching the viewer,
      Neo4j and the models on first use (`graphme.setup()`), the LangChain loader, workspaces
      from code, `docs/library.md` and the notebooks, and PyPI (the owner's pending publisher).
- [ ] **A team workspace.** A workspace kept in a folder on a shared drive (Box, Google Drive,
      OneDrive/SharePoint), so a team opens the same graph. That folder is left out of what a
      build reads, whatever folder above it is chosen. To decide first: a running Neo4j's store
      and transaction logs cannot sit in a folder a sync client rewrites, and two machines cannot
      open one graph at once (Community is a single writer) -- so the shared folder may hold a
      snapshot (`.dump` or import bundle) that one member publishes and others open read-only,
      with the live graph staying local.
      (Put off 2026-11-05.) Wanted: a workspace a team shares as in Figma or ChatGPT Team --
      everyone asks the same graph and adds documents to it. Shape last discussed: only the
      graph (a shared Neo4j: Aura, or a small server) and the crops (shared object storage)
      leave the PCs, and each member's app still reads, renders, embeds and answers. Open: who
      can preview or open an original that only its uploader holds, one embedder for the
      team, and roles (Community and Aura Free have none, so a member who can reach the graph
      can delete from it).
- [ ] **Mail services as a source.** Part A is done: IMAP (presets, app password), counted and
      asked before the first read, newest first, new mail read while the app runs, one mail
      workspace per account, a message shown with its attachments. Part B: Gmail is done (2026-11-14:
      Google sign-in in the browser, PKCE, `gmail.readonly`, read through the Gmail API into the
      same store; tested against a fake Gmail only). Open: a first sign-in with a real account
      (the owner's test user); where the project's Google client ships -- today
      `GRAPHME_GOOGLE_CLIENT_ID`/`_SECRET` from `.env`, so the public build has none and offers
      IMAP; Google's app verification past 100 users; Gmail's history API instead of listing every
      id each check once mailboxes are large. Outlook through Microsoft Graph is done
      (2026-11-14: app registration "Graph-Me-Maybe", client id in `config.MICROSOFT_CLIENT_ID`,
      any Entra tenant and personal accounts, `Mail.Read` + `offline_access`; tested against a
      fake Graph, a real sign-in reached Microsoft's consent page). Open: a first read of a real
      Outlook mailbox; publisher verification (an MPN id), without which a work account's users
      cannot consent to a new multitenant app and need their admin; Graph's delta queries
      instead of listing every id each check.
      Refinements:
      unpacking Outlook's TNEF (`winmail.dat`), and a time and cost estimate in the first-connect
      question (today it shows counts and size only).
      - (Done 2026-11-14) A calendar invite is read as text; Outlook for Windows' From:/Sent:
        header quotes what follows in an HTML body; "Disconnect, keep workspace" keeps the
        messages; a mailbox with no mail reads to done.
      - A file nested in an attachment outlives its last message. `carried_only_by` follows one
        level (message to attachment); the next needs the `embedded` links of the attachments
        going, and a test against a real Neo4j (none in `tests/public` today).
      - Connecting counts the mailbox inside the request: no progress for a large mailbox.
      - One mail workspace by default, with several mail sources picked to connect into it
        (today each account makes a workspace of its own).
- [ ] **A conversation, not one question at a time.** Ask takes a single question today (`/?q=`,
      `/v1/ask`), and a follow-up ("and the one before it?", "make that a table") starts over
      with no memory of what was asked or answered. Keep the turns of a conversation, so a
      follow-up is read with the earlier questions, answers and the documents they cited. To
      decide: how much history goes back to the model (a token budget, the oldest turns
      summarised or dropped), where a conversation is kept (per workspace, on this machine), and
      how the address and the found-documents folders show more than one question.
- [ ] **Search only when the question needs the documents.** Every question today runs the
      search and answers from what it found. Decide first, per turn, whether the documents are
      needed: a question about them (a value, a procedure, a document number) searches the graph
      as now; one that is not ("thanks", "translate your last answer", "what does MPa stand
      for?", a rewrite of the answer just given) is answered by the model alone, with the
      conversation as context, and says it did not search. To decide: who judges (one short model
      call, or the answering call itself choosing a search tool, as an MCP client does), what is
      shown when it chose not to search, and a way for the user to make it search anyway.
- [ ] **Graph-Me-Maybe inside other apps.** A side panel in the app already open that lists the
      documents related to the open mail (search only, no model call), answers a question with
      sources, and inserts it into the reply or the document -- talking to the Graph-Me-Maybe on
      `127.0.0.1`, so nothing leaves the PC. An Office add-in (Outlook, PowerPoint, Word, Excel;
      task pane page on graphme.io) and a browser extension for Gmail, which also adds a "GMM"
      toggle to the search box of Gmail and Outlook on the web. Outlook desktop's own search box
      cannot be taken over by an add-in (a ribbon button opens the pane instead). Needs on the
      server: pairing a client once in the app, its token in `keyed`, CORS for paired origins
      only, the MCP workspace switch reused for every outside client. Open: Office on the Mac
      (WKWebView and `http://127.0.0.1`), store accounts. Design:
      `docs/superpowers/specs/2026-11-13-mail-add-ins-design.md`.
- [ ] **Searching every workspace at once.** Direction (2026-11-13): Graph-Me-Maybe gathers mail,
      cloud drives and this PC into graphs, one per workspace, kept apart, and serves them as a
      private MCP server to the user's own agents (Claude Desktop, Claude Code, Cursor) -- later,
      as a SaaS, the graphs in our cloud behind an MCP server we run. A search may span every
      workspace or one. First way, nothing new on the server: the agent calls `search` once per
      workspace (`workspace=`) and merges what comes back. Measure it first with three or four
      workspaces, since a workspace not open may have to start its graph DB; a light per-workspace
      search index (passages, vectors, words outside Neo4j) only if that proves too slow.
      Measured 2026-11-14 (Windows, dev build, four workspaces of 0-19 documents, one question
      asked of each in turn as an agent would): the open workspace answers in 0.3 s (5.5 s the
      first time), every other one in 10-14 s -- its graph DB is started beside the open one, and
      only one is kept there, so the next workspace asked stops it. All four took 35-47 s a
      round. Too slow for an agent to ask every workspace each turn. Next, to decide: keep several
      graph DBs running beside (each a Java process with its own heap) or the light search index
      above, which needs no graph DB to search.
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
      -P`); Google Drive and Box Drive there keep what is read in their own capped caches. The
      QA of 0.1.68 found a personal OneDrive and showed its folders and usage, but that PC's
      OneDrive was signed out, so no file could be made online-only: run it on a PC with
      OneDrive signed in and syncing.
- [ ] Run each cloud drive end to end with a real account, not invented folders: Box (the desktop
      folder and the API picker, built and previewed), Google Drive for desktop, OneDrive, and the
      Other drives page's Dropbox, iCloud Drive and Synology Drive (added 2026-11-14; their
      Windows folders are the clients' defaults, unchecked on an install), each with files kept
      online only -- found, read, given back, previewed again, downloaded. The owner runs them
      together later.
- [ ] (Later) ONLYOFFICE as a source. It has no desktop client that syncs files to a folder, so
      it cannot be read like Box Drive, Google Drive or OneDrive, and DocSpace has no WebDAV
      server yet. Read DocSpace through its REST API with a personal access token (its address
      and key in Keys and model; rooms and folders browsed in the app, picked files fetched while
      read). A room kept on an outside storage (OneDrive, Box, Google Drive) is read through that
      drive's tab already; Workspace's WebDAV can be mounted and added as a folder.

## 1.6 · The background app and MCP on Windows

- [x] (Closed 2026-11-05, kept as a record) Windows: the tray menu, the first-close notice,
      opening again from the Start menu, a start at login, the installer ending the running app
      (and one from before 0.1.57), Open original in Excel, Word and a PDF app, `open_document`
      from an MCP client, and the page following MCP and workspace changes made outside it.
      Checked on Windows 10 22H2 x64 against 0.1.68, the fixes in 0.1.69. OneDrive is left
      under 1.5.
- [ ] Windows: an MCP client built on the Python MCP SDK runs its server in a job object that
      kills everything in it when the client closes, so the app `graphme mcp` started there ends
      with it (seen 2026-11-08). Claude Code, Claude Desktop and Cursor are Node and break away
      from their jobs: not affected. A fix would start the agent outside the job (breaking away
      needs the job to allow it, which the SDK's does not) -- e.g. through the scheduler or a
      service; worth it only if a Python-based client matters.

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
      relations and evidence. Started 2026-11-14 on the invented samples
      (`tools/golden/samples.yaml`: units and where they are, texts kept apart, children under
      parents, the links by document number, six questions with their files and the words of
      the answer). Left: documents the samples do not cover -- a PDF of its own, a scan, a
      picture or chart, a table across pages, a Hangul document -- each made up for it.
- [x] A golden evaluation measuring mapping and layout, split, relations, links, evidence
      recall, citation and the answer **separately** (`tools/golden/evaluate.py <server>`: builds
      the samples into a workspace of its own and prints each measure with its misses; the
      scoring is pinned by `tests/public/test_golden_evaluate.py`). It calls the configured
      model, so it is run by hand, not in CI. First run (dev build, 2026-11-14): every measure
      whole except links. The misses are by rule, not by the model: the samples' numbers
      (`GS-100`, `QP-210`) have three digits, under `MIN_NUMBER_DIGITS` (4, kept so `SHA-256`
      and `COVID-19` name no document), so a name like "GS-100 Valve Part Basic Specification"
      whose cue is only itself is dropped (`_written`), and a bare "per QP-210" is not a
      number at all. To decide: whether a number that matches a document in the space should
      count whatever its digits, or the samples should carry four-digit numbers.
- [ ] TODO (held for the owner's call): the document number rule above -- a number matching a
      document in the space whatever its digits, or four-digit numbers in the samples. Links stay
      3/7 in the golden run until it is decided.
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
- [ ] Move the viewer's pinned versions forward (today 0.86.1) once upstream ships the
      smoothed-line axis and the combo on a hidden secondary category axis: our #1983 and #1984
      were merged together as #1991 into the `v0.89` branch on 2026-10-30, for the next 0.89.x
      patch release after 0.89.7 and then `main`. Update `UPSTREAM`'s `commit` and
      `npm_version`, then drop
      `chart_axes.py`, `picture_anchors.py` (fixed upstream in `d7f60b865`) and dist patch
      `0003`, and check every other patch still applies and is still needed. (2026-11-14: tags
      v0.89.8 to v0.89.11 all hold #1991's merge `bd1c008` and `d7f60b865`, so 0.89.8 or later
      will do -- 0.89.11 is the newest. The build needs Rust and wasm-pack, which the Windows PC
      does not have: run it on the Mac, then check every format's samples render as before.)
- [ ] HWP and HWPX are read from rhwp's print (`hwp_print.py`, `@rhwp/core` 0.9.0 as published).
      rhwp leaves some table cells empty and cuts a wide table on a landscape page at the
      portrait width; its own text APIs do not show it (`getPageText` skips tables,
      `getPageTextLayout` is what it drew, `getTextFileText` lacks the same cells). Since
      2026-11-14 the file's own paragraphs (HWPX section XML, HWP `PARA_TEXT` records) are
      checked against the print and those missing are set on pages after it (`keep_undrawn`):
      read, but with no place on the page and outside the table they belong to. Left: a
      paragraph holding a character the built-in Korean font lacks (`ㆍ`) is set but reads
      back without it. Report the dropped
      cells upstream with an invented reproduction, and check each rhwp release against such
      pages (`UPSTREAM`'s `hwp_version`); the fewer it drops, the less goes after the print.

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
- Every change is made here and reaches Graph-Me-Maybe only through `tools/publish/export.py`.
  A fix committed there by hand is overwritten by the next export (0.1.69's Windows fixes were,
  until brought back here).
- A fake in the test profile is not live verification.
- When only documents change, run only the markdown and link checks and `git diff --check`.
  Run Ruff, mypy and the tests only when source is changed with them.
