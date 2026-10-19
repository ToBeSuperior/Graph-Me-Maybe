# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## The rule that shapes everything else

**Nothing measured from a source document belongs in this repository.** Sheet names, item
labels, cell ranges, node/unit counts, character counts and timings are facts about a
customer document, not about the method. They do not go into code, comments, docs, commit
messages or test fixtures that are tracked.

This is why three paths are gitignored and are **not** missing by accident:

| Path | Holds |
|---|---|
| `/tests/` (except `tests/public/`, `tests/fakes/`, `tests/__init__.py`) | the local suite — its fixtures and golden files assert measured values |
| `/docs/progress-local.md` | the experiment log: what was tried, what broke, what changed |

A fresh clone has `tests/public/` (invented data only; no key, Neo4j or network) and the
`tests/fakes/` it uses; the rest of the suite stays on the machine that measured it. A test
moves into `tests/public/` -- never copied -- only when every literal in it is invented.

Also gitignored and absent from a clone: `/test_dataset/`, `/reports/`, `/data/`, `.env`.

## Commands

```bash
uv sync --extra dev                    # install
uv run graphme --help              # every command; xlsx-bundle renders one workbook to pages
uv run graphme build-graph <folder> # every document into the Neo4j graph space, linked, embedded (--no-embed)
uv run graphme embed-graph          # cut the space into passages, embed them (embedder's key)
uv run graphme ask "<question>"     # GraphRAG over the space (neo4j-graphrag)
uv run graphme export-corpus <dir>  # neo4j-admin import bundle: graph, passages, vectors, schema
uv run graphme app                  # the installed app: samples, its home folder, the browser
uv run graphme serve                # /v1 (jobs, search, export), /docs, the web app; 127.0.0.1:8765

uv run ruff check .                    # line-length 100, target py311
uv run mypy src                        # strict = true

uv run pytest tests/public -q          # what a clone has; `uv run pytest -q` adds the local suite
uv run pytest tests/path/test_x.py::test_name -q     # one test
uv run pytest -m offline               # markers: offline, e2e
```

The web app (`frontend/`: React, Vite, Tailwind, Radix, TanStack Query, vis-network), served
same-origin by `graphme serve` or the demo (`python tools/hierarchy_demo/app.py`, :8765) from
`frontend/dist`:

```bash
npm ci && npm run typecheck && npm test && npm run e2e && npm run build
npm run dev     # :5173, /v1 proxied to the server
npm run api     # regenerate src/api/schema.d.ts from `graphme serve --openapi`
```

Pages are drawn by Chrome (or Playwright's own Chromium) through Playwright for Python;
`frontend/` keeps `@playwright/test` for its e2e only. The e2e checks run
against a fake API (`e2e/fake-api.ts`) with invented data -- never a measured value.

Rendering DOCX/PPTX/XLSX needs Chrome, or Playwright's Chromium, plus a one-time viewer build:
`src/graphme/adapters/rendering/viewer/setup.sh` (it also applies `viewer/dist-patches/`
to the published build; an already-built bundle needs them applied by hand). It builds into its
argument, else `GRAPHME_VIEWER_DIR` from the environment or the repo's `.env`, else the cache. If `rust-lld` aborts with
`Library not loaded: @rpath/libLLVM.dylib`, that README has the symlink fix.

## Architecture

Ports and adapters. `domain/` holds frozen Pydantic models (`extra="forbid"`) and is
dependency-free; `ports/` declares Protocols; `adapters/` implements them; `application/`
orchestrates; `runtime.py` is the only place that wires concrete adapters together, and
both the Typer CLI and the FastAPI app go through it.

### The document graph pipeline

`application/document_graph.py` (`DocumentGraphBuilder.build`) is the spine, and the order
matters more than any single module:

```
extract → validate → visual binding → validate
        → [derived only] figure grouping → widen figures → validate
                       → text grouping (PDF, PPTX) → validate
                       → outline decisions + figure-part placement
        → enrichment (captions, references) → validate
        → put in the graph space → link mentions (SAME_AS)
```

A rendered page is asked three separate questions, and the order is the point: which
units are one figure (so a figure takes the words written on it), then which pieces of
what is left are one paragraph, then what reads under what -- an outline drawn over
half-units places half-units. A figure's parts (`part_of_figure`) are placed under it by
rule (`figure_part_decisions`), once, by the build; the outline pass is not asked, and no
longer asks for figures at all. When the page says several tables are one table,
`widen_figures` also gives it the text lying inside the merged frame (a band label between
the parts); pictures do not get this, since body text can stand between two photographs.
What a visual holds is decided in one place, `page_units.held_by_a_visual`.

`ExtractedDocument.validate_canonical_semantic_graph()` runs between stages, not once at
the end. It is deliberately separate from model validation so previously stored graphs
stay readable while newly emitted data is held to the stricter contract.

### Native-first authority

Extraction owns the graph. Rendered and VLM observations may only **add** evidence to
conservatively matched native nodes; an unmatched observation is not promoted. There are
no page nodes in an authoritative graph — pages are a rendering artifact, not a semantic
parent — and every persisted `NEXT` edge must join siblings under one `CONTAINS` parent.

`NEXT` is only for an order the file itself numbers, which today is slides: slide 2 follows
slide 1 and the deck says so. A worksheet's tabs are separate, not a sequence, and what
follows what inside a slide, a page or a body of running text is the outline pass's
question, not a fact to persist.

Two edge types are easy to confuse: `CONTAINS` is native ownership and is immutable;
`OUTLINE_PARENT` is the derived semantic outline and is the only thing a hierarchy
decision may rewrite.

### Rendering

`adapters/rendering/browser_office.py` draws OOXML in a browser (WASM parser on a
Canvas). There is no LibreOffice and no Office-to-PDF step. The renderer records column
and row edges as it draws, so cell→pixel is a **lookup**, never an estimate, and an XLSX
sheet maps 1:1 to a page. PDFs are rastered directly by PyMuPDF.

Every read of a PDF page's text goes through `extractors/pdf.py` `page_text`: some PDFs write no
space between words and only move the pen, and pymupdf fills that gap for a Latin font but not
for a CJK one, so Korean ran together. A gap over `WORD_GAP` of the font size is a space.

Each render serves from a site of its own under its temporary workspace, on a port the OS
assigns (`browser_capture.py`, one handler serving the render's files over the bundle -- no links). Copying the document
into the shared bundle let concurrent renders overwrite each other's input and return the
other document's pages with no error.

A picture or chart is measured by its DrawingML `ext`, not by the cells it is anchored to — it
does not fill them, and `editAs="oneCell"` leaves the `to` marker stale. A chart carries no
position of its own in openpyxl's order, so it is matched to its drawing frame by anchor
(`_chart_frame`); without that it had no box, no crop, and never reached the hierarchy pass.

A worksheet is read through `written_cells()` — the cells the file actually wrote, in reading
order — never through the dimension Excel declares, which reaches a whole column past the last
thing on the sheet and costs an openpyxl cell object per position. A drawing's axes are
measured as far as its anchors reach, plus a margin, and carry on in the last size after that.

Pictures inside an EMF or WMF are DIBs, and the viewer decodes them itself. A depth it cannot
read is not an error there — the metafile simply draws its lines and leaves the photographs
out, silently — so a picture that renders as frames and arrows alone means the decoder, not
the file. Both viewer builds carry their own copy of it, so a fix is two patches
(`viewer/patches/` for XLSX, `viewer/dist-patches/` for DOCX and PPTX).

### One producer of a crop

`application/xlsx_visual_binding.py` is the **only** thing that cuts a node's crop.
Whatever overlaps or touches becomes one region (union-find in
`application/xlsx_unit_regions.py`), the region is cut once, and the asset hash plus box
go on the node that names it. Every reader calls `read_node_crop(node, asset_store=…)`
from that same module and passes a **node id, never a box**.

This cost two bugs. Two places merged the same pictures independently — one on the real
extent, one on cell ranges — elected different owners, and the hierarchy pass ended up
showing the model one node's id beside a different node's photograph. If you need a crop,
read the stored one; a new consumer that cuts its own is the next divergence.

Extraction judges nothing about text. Every format reports text as `TEXT_CHUNK` -- no
HEADING, CAPTION or SECTION, no `heading_level` -- and ownership (`CONTAINS`) is only what
natively holds a unit: its sheet, slide or document, or the group or chart a drawing is
part of. What reads under what is the hierarchy pass's question alone. Worksheets had
this decided first; slide titles, Word heading styles and PDF font sizes kept producing
headings, a PDF built a SECTION tree from font sizes, and the worksheet extractor still
hung every unit under marker-derived headings in `CONTAINS` -- where retrieval falls back
whenever no derived outline exists. `tests/extractors/test_flat_text_nodes.py` holds the
rule for all four formats.

### Hierarchy

`build-graph` and the demo always read the outline off the page (`derived` in `graph_parts`).
The marker-based pass was removed because a marker is display text and an outline nobody can
trust is worse than none.

`derived` reads the outline off the rendered page, one model call per scope, for every
format in `SCOPE_FORMATS` (XLSX, PPTX, DOCX, PDF). `UnitPlacer` is the only part that
differs: cell edges for a worksheet, the shape's own rectangle for a slide, the body index
each rendered run came from for DOCX (`FlowPlacer`), the normalized bbox the extraction
recorded for PDF.

A sheet or a slide is a unit the author worked in, so its page is its own tree. A page of
DOCX or PDF (`FLOW_FORMATS`) is where the paper ran out: the document node is the root, the
pages are read in order, and each one is given the outline the pages before it built plus
that page's bottom strip -- so the outline spans the document. A paragraph or table drawn
on two pages is decided once, on the page showing most of it.

What travels is the outline itself, not a chain. Sent only the open ancestors, every page
after the first decided the document's top level again from the picture, and the same kind
of heading landed under the title on one page and under the line above it on the next; a
heading arrives now with what reads under it, which is what says it is the same heading.

Three things go out, in this order (`_Placements.carried`). The open chain -- the path
from the document down to where the page before stopped, which is where this page attaches.
The document's headings (every unit that holds something), shallowest first, up to
`SKELETON_SHARE` of the budget, each only with its parent already carried. And whole recent
subtrees in what is left: a subtree travels with every descendant or does not travel.

The headings exist because carrying only whole subtrees, newest first, dropped a long
document's early chapters out of the reference entirely: a late page had no idea what level
the document's chapters stood at. A heading that arrives without its items carries how many
it holds (`outline_omitted`), or it reads as a section that began there and holds nothing.
A unit is never carried without its parent: it would read as a place to attach to and not
be one. Headings are sent at `CARRY_LABEL_CHARS`, units that hold nothing at
`CARRY_ITEM_CHARS`, and the budget is counted on what is actually sent.

`CARRY_TOKEN_BUDGET` (1,200) decides how much fits -- twice a search unit, because this is
a reference the page reads its own units against. Korean is about a token a character, so
the estimate counts Hangul separately; bytes over four puts a Korean outline at a third of
its size and the budget would never be reached. The newest subtrees are kept first: a
section decided two pages ago is where this page continues, one from the first page is not.

What a picture holds is kept out of the open chain: a page that ends inside a figure
otherwise carries a word printed on the drawing as the innermost thing still open, which is
what the next page is told to read under. The figure still travels as part of what its
section holds.

Text grouping (`application/unit_grouping.py`) explodes PDF blocks into printed lines and
slide shapes into paragraphs, then asks `PIECES_PER_ASK` pieces at a time, carrying the last
`CARRY_PIECES` of the previous ask as settled. Small print -- smaller than the page's modal
size, with no body text above or below it in the same x-span -- is moved to the page's end
and never carried. A joined unit's box is measured only from pieces on its first piece's
page and column (`_around`), or a paragraph continued in the next column reads as if it
stood at the top of the page. The answer lists each group's piece ids; a range was tried and
made over-joining cheap. An overlapping or unresolvable group is dropped alone.

Both grouping and the outline scope order a page with `application/reading_order.py`: bands
cut by whatever spans `SPANS_THE_PAGE` of the width, columns by x-overlap, each column read
to its foot. Sorting by top edge zigzags a two-column page. `indent` is ranked from each
unit's own column edge (`in_columns`), not the page's.

The outline prompt says a document's title holds the document -- authors, abstract and
every section -- and only furniture (running header, page number, watermark) stands beside
it at the top. Short ids resolve an exact match, one character apart, or the last two
characters wrong (`_tail_slipped`), each only when exactly one unit fits.

Flowing pages are only read in order: reading them side by side and stitching the page
breaks afterwards was faster but lost cross-page parents the ordered reading keeps. Sheets and
slides are independent scopes, so those are what a caller reads in parallel. Every model call
takes the provider's `limiter`, so a caller reading several documents caps the calls in flight
across all of them.

Two measurements go out beside the labels, and both exist because the picture alone was not
enough. `indent` ranks where each unit's words start (0 leftmost), counting the paragraph
indent *and* the spaces written inside the line -- one report nested entirely with spaces,
which `text.strip()` had been discarding. The page image is cut to its ink plus a 5% margin
(`_trimmed`); only the picture changes, since crops are cut from the untrimmed page.

Traps this cost, in the order they bit:

- The renderer reports a drawing under the body index it hangs from. Keyed with the
  paragraph's own runs, a line of text takes the picture's rectangle and then swallows
  whatever stands in that band. Paragraph boxes come from runs; drawings from
  `flow_drawings()`.
- A PDF's border parses as a table, and the finder stops at the outermost one. Frames are
  reopened rather than reported (`_tables_of`), and a frame is told from a table by its
  contents -- mostly empty, one cell holding a paragraph -- not by its size.
- pymupdf groups text into bands, so a left-column paragraph arrives holding the cells of
  the table beside it. `_outside_tables` drops the lines that fall inside a table.
- A highlight is fills painted behind words, and two fills that meet read as a one-row table
  of the highlighted phrase. A one-row table no taller than twice its text is dropped
  (`_is_highlight`), so the words stay in their paragraph.
- A drawn table needs its border, but not every unit of it: one cell of a long row that its
  author never formatted rejected the whole rectangle, and the sheet came out as loose text a
  cell at a time. A side is drawn with a gap in proportion to its length (`PERIMETER_GAP`),
  and a short box still needs every unit of its own.
- A diagram's frame and arrows are vector paths and never become nodes, so a figure takes
  in the ink it touches (`page_ink`, `grown_by_ink`) before the merge -- which is also how
  it comes to own what that frame encloses. A table's grid is ink too: ink inside a table
  frame is left out, or a photo in a cell grows across the grid and owns the table.
- Only text is sized by where its glyphs drew. A table sized that way ends above its rows of
  photographs. And the renderer numbers layout shapes in their own space, so a glyph box is
  used only when it lies in the shape's own frame (`NormalizedBoxPlacer`).
- Text never names a region that holds anything else (`build_unit_regions`); a DOCX table's
  box includes the drawings rendered in its cells (`FlowPlacer`).
- A region's rectangle grows over ground none of its parts covers, so text is judged against
  each table's frame and the drawings' union, never the region's rectangle
  (`merge_regions`). A line (thinner than `LINE_PIXELS`) is a connector: taken in by a table
  frame or the picture that encloses it -- judged the same way -- never joining what it touches.
- Shape numbers repeat within a slide (a pasted group keeps its children's numbers). A
  connector naming a number two shapes share joins only the one it reaches (`_joined_ids`),
  and a join never pulls text into a region -- text belongs only by lying inside.

A decision is `parent`, `root`, `unresolved` or `drop`. `drop` takes what a page carries
without saying anything -- a page number, a repeated footer, a watermark, a decorative rule
or picture -- out of the graph with everything it owns (`apply_hierarchy_decisions`); a unit
the same answer placed under a dropped one reads at the root, and a dropped unit is never
carried to the next page or taken as where a page stopped. It was taken out once (a wrong
call loses content no later pass gets back) and brought back when page numbers kept turning
up as a linked document's nearest passage; the prompt asks to drop only what would be noise
if a search returned it. Passages are the second guard: a stretch with no word in it, and a
number standing alone under the document, are never a passage's text (`graph_chunks.py`).

`GRAPHME_HIERARCHY_READINGS` (default 1; the demo 2 unless `.env` or the environment sets it,
via `model_fields_set`) takes that many readings of each page at once.
Identical readings are kept as they are -- nothing to settle costs nothing. Where they
differ, `choose_hierarchy_scope` sends the page back with both outlines written in the
same shortened ids and keeps whichever one it picks, whole. Never merge two readings: a
hierarchy assembled from parts of both is one nobody judged.

A page the model gives no usable answer for is skipped rather than failing the document,
and `derive` returns it in `HierarchyDerivation.unread_pages`; the graph build turns each into a
`hierarchy_page_unread` warning. A page left flat because it failed must not look like a page
read as flat.

The endpoint answers 408 under concurrent load and 503 (`server_is_overloaded`) when upstream
is busy, for minutes at a time. Transient statuses and transport errors say nothing about the
page, so they are retried on their own schedule (`TRANSIENT_RETRY_DELAYS`, several minutes in
all), counted apart from the attempts a malformed answer gets; a 20-second window left pages
flat through ordinary overloads.

What the model is sent, and why, is in `docs/method.md`. Two constraints are easy to
break: a unit is addressed by a **shortened** id (`short_ids()` in
`adapters/vision/chat_completions.py`) because a full UUID is not something a model can copy
back, and the response asks for nothing the prompt did not supply — an echoed identifier
proves nothing when this side is the one that sent the request.

### Captions

`application/visual_captions.py` writes what a table, a chart or a picture shows, which the
document's own words do not say. The page goes to the model with the stored crop of each
visual on it (`read_node_crop`, never a fresh cut), and each answer becomes a
`vlm_enriched_caption` node: held by the visual (`CONTAINS`, so it has the one parent every
node needs) and pointing back at it with `CAPTION_OF`. Its evidence is `GENERATED_CAPTION`
and it is not retrievable -- what a reader asks for is the visual. Pages are described side
by side and every call takes the caller's `limiter`. A page the model gives nothing for is
left without captions rather than failing the document. A caption is written in the language
of the page's running text, not of words inside the crop -- an English paper showing a Chinese
sample page was described in Chinese.

Page noise (a watermark, a header icon on every page) is the outline pass's `drop` to take out.
A rule removing what repeats in the same place across pages was considered and rejected: one
rule applied to every document deletes content in some.

A flowing document's drawing knows the body element it hangs from, not the page it landed
on, so DOCX passes `page_of` from the same layout the crops came from.

### Questions over the graph space

`adapters/storage/graph_rag.py` sits on the graph space and shares its
driver. `embed-graph` cuts each document's outline into passages
(`application/graph_chunks.py`: a section that fits `CHUNK_TOKENS` is one passage, a larger
one is cut between its items under its headings, a single unit too big is cut between its
lines) and the space stores them as `Chunk` nodes that `COVERS` their units; one node at a
time said too little to be found. Each passage records `CHUNK_RULE`, so changing how
passages are cut re-cuts every document on the next run -- bump it when `chunks` changes.
The vector index is kept at the embedding model's size; an index at another size is
dropped, since it would hold none of the new vectors. `ask` runs neo4j-graphrag's
`HybridCypherRetriever` (built once, on the first question) -- the vector index and the
`chunk_text` fulltext index together, since a vector ranks a passage holding the document
number a question names below passages that only sound alike -- and walks
`REFERENCES -> SAME_AS` to bring the cited document's nearest passages along. The two
rankings are fused by weighted reciprocal rank fusion in our own query (`_FUSED`, k =
`GRAPHME_RRF_K`, vector weight `GRAPHME_HYBRID_DENSE_WEIGHT`, flipped toward the words for a
question naming a document number), each index searched well past top_k; the library's own
max-normalised ranker is not used. The fulltext index uses the `cjk` analyzer -- the standard
one keeps a Korean word whole with its particle -- and an index made otherwise is dropped and
made again when the space opens. Rank cannot say whether anything was found, so a
passage is fused only with evidence: cosine at least `GRAPHME_RELEVANCE_FLOOR`, or it holds
every word of the question or the number it names (`every_word`); none, and nothing is found. The embedding
key is the embedder's own variable (`EMBEDDERS[...].key_env`); the answer comes from the same model endpoint as
every other call. Prompts are English; the answer follows the question's language, and is English when the question shows none (only numbers or codes).

Phase 1 ends at the export: `export-corpus` writes a `neo4j-admin database import` bundle
(`adapters/export/neo4j_import.py`) -- typed CSV per label set and relationship type, the
passages with their vectors, `schema.cypher` for the constraints and the vector and
fulltext indexes, and `import.sh`. Loaded into an empty Neo4j, Neo4j's own retrievers work
on it with no code from here. Vectors never go in a Cypher script: the server parses every
literal, and a space's vectors in a script ran a 1 GB server out of memory.

`--dump` (and the demo's export dialog) turns that bundle into one `.dump` with
`adapters/export/neo4j_admin.py`: the app's own `neo4j-admin`, on a scratch folder of its own,
imports the bundle, a scratch Neo4j is started only to make the indexes (a
dump carries them, the import does not make them), and dumps. That file is what Aura's
Import Database, Neo4j Desktop and `neo4j-admin database upload` take. An upload replaces
the Aura instance's data and sends the documents off this machine, so the demo requires a
`*.databases.neo4j.io` address and an explicit confirmation; the password reaches
`neo4j-admin` only as `NEO4J_PASSWORD`, never on a command line or in a message.

### The web app

Ask is the main screen; Manage (library, links map, a document's outline and pages, export,
danger zone) is where the graph is looked after. A question lives in the address
(`/?q=&k=`); its search (`/v1/search`) and its answer (`/v1/ask`) are asked side by side, and
the found documents are folders by hop (`lib/hops.ts`). Every API shape is in
`src/graphme/server/models.py` and reaches the app as generated types -- a field the server
sends without declaring it there is dropped. The demo's `POST /v1/samples` is the one route
outside that contract (`Samples` in `src/api/client.ts`). The build and the export shown are
the newest job of each slot in `GET /v1/jobs` (`lastBuild`, `lastExport`). Words are in `src/lib/words.ts`, English
typed as Korean's shape. Colours are tokens in `styles.css`; what is painted on a canvas reads
them when drawn (`token()`) and draws again on a theme change.

The server is `src/graphme/server/`: `app.py` (`create_app`: the `/v1` routes, the key, the
error handlers, `/health`, the web app's pages; `serve`), `state.py` (`Demo`, `Session`,
`CorpusBuild`, `ExportJob`, the job registry and the threads that read and build),
`settings.py`, `models.py`. The demo lives in `src/graphme/server/demo.py` (the `POST /v1/samples` route via `extra=`, two readings a
page and a ten-minute render in `demo_settings`, also passed as `load=` so a save keeps them, and `run`);
`graphme app` (`src/graphme/launcher.py`: samples, its home folder, the browser) and the checkout's
`tools/hierarchy_demo/app.py` (the checkout's `.env`, Neo4j warmed at start) both run it.
Startup logs `preflight()` -- an empty graph embedding key, a chat endpoint that refuses the key
or does not answer (`GET /models`, no model call), a missing viewer bundle. The same check backs
`GET /v1/settings`; `PUT /v1/settings` (the Manage › Keys and model page) takes only `EDITABLE`
names, tries a changed key first, writes `.env` (`write_env`, mode 600) and swaps the settings
in place (`Demo.reconfigure`) -- a secret is never sent back, and a request not from a loopback
host and page is refused (`from_here`). With `GRAPHME_API_KEY` set, a 401 opens the key prompt
(`components/KeyGate.tsx`): the key becomes the session cookie `graphme_key` (`SameSite=Strict`)
and the page loads again -- an `<img>` or a download link cannot send a header. The outline graph keeps one vis network per layout, view and theme; a fold or a
toggle changes its data in place (`graph/draw.ts` `visElements`, `replaceAll`).

The outline graph places nodes with `src/lib/graphLayout.ts`, kept free of the page and vis
(`graphLayout.test.ts`): a radial tree whose wedges never cross or overlap, and folding -- any
node folds its items (a visual its parts) into a triangle marked +N; one holding more than
`FAN_LIMIT` starts folded.

The samples (`src/graphme/samples.py`, also `python -m graphme.samples <folder>`) are invented documents written on request,
byte for byte the same each time (fixed zip and core-property times), so asking twice finds
them in the graph instead of reading them again. They are the demo's: the web app offers them
only when `GET /v1/settings` says `samples`.

### The local Neo4j

No Docker: Docker Desktop on Windows runs dozens of CLI plugins every twelve seconds while any
container runs, and the PC stalls each time. Settings naming no `GRAPHME_NEO4J_URI` get
`local_neo4j` (the app's home), and `runtime.create_graph_space` runs that home's own Neo4j
(`local_neo4j.py`): Neo4j Community `NEO4J_VERSION` and a Temurin JRE fetched into `neo4j/` and
`jre/` (checksums checked; by the installer, or on first use), the graph in `neo4j-data/` so an
update never touches it, started as a child (`neo4j console`, its own `NEO4J_CONF`, auth off,
bolt on 127.0.0.1 at the first free port from 7687) and stopped when that process ends -- atexit,
or `stop_started()` before the window app's `os._exit`. A Neo4j answering on the port the home
recorded is adopted only if `server.directories.data` is this home's (WSL's app answers on
Windows' loopback too). The server never opens it in a page's request: `Demo.space(wait=)` opens
it behind and answers `GraphStarting` (503, Retry-After), which the page keeps asking through.
This checkout's `.env` names its own `GRAPHME_HOME`, so development keeps a graph apart from an
installed app's. An `export-corpus` bundle leaves out `source_path` (this machine's paths never
leave it), so a graph moved through one has no files to draw or download until those are set again.

### Neo4j shape

`adapters/export/neo4j.py` is the one mapping to Neo4j, and the demo's graph view draws the
same records it produces. Every node gets the shared label `DocNode` (the `id` uniqueness
constraint lives there, so a relationship MATCH can use the index) plus its type as a second
label; properties are scalars or scalar arrays only, nested values are JSON strings, nulls are
omitted, and node metadata is prefixed `meta_` so it cannot shadow a core field.

### Providers

Every model call goes through `adapters/chat.py` `complete`, which writes the request for the
provider's wire: OpenAI's chat completions (OpenAI, Gemini's compatible layer, any server
speaking it) or Anthropic's Messages API, with images as data URLs and the answer held to a JSON
schema. Who answers is `model_endpoint.py`'s table: each provider's wire, address, key
variable and default model, and each embedder's address, key, model, size and input limits.
It is the one module the public tree replaces. `config.py` validates the base URL through it
and rejects fake providers unless `GRAPHME_PROFILE=test`.

`GRAPHME_PROVIDER` is `openai`, `anthropic` or `gemini`; its key is read from that provider's
own variable (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) and required at
startup. Embeddings come from OpenAI or Gemini; with Anthropic, search needs an OpenAI key or
`GRAPHME_EMBEDDING_PROVIDER=gemini`. `GRAPHME_MODEL_BASE_URL` can name another server speaking
the same wire: over HTTPS anywhere, or plain HTTP only on this machine.

Offline results prove orchestration, persistence and readiness — never provider quality.


## Where to read next

- `finalized_source.md` — the confirmed pipeline, design principles, processing rules, and
  how to verify a change (no measured values, by policy).
- `docs/method.md` — why the pipeline looks like this; the failures that shaped it.
- `docs/roadmap.md` — what is still open.

## Working style

RED before GREEN, and make the red failure a behaviour difference rather than a collection
error. A red test that passes on the first run means the fixture does not reproduce the
problem — fix the test before touching the code.

After changing a VLM payload, intercept the adapter's real HTTP body (`httpx.MockTransport`)
rather than rebuilding a copy of the prompt; a copy cannot show you where it has drifted
from the adapter. When judging how well the model read a document, look at the rendered
page, not only at the payload and the resulting tree.

