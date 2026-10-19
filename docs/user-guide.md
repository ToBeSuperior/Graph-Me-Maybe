# User guide

A tour of the app: putting documents in, asking, reading the results, and looking after the graph.
The app is in English by default; switch to Korean in the settings menu (top right). `⌘K` (Ctrl+K)
jumps to the question box from anywhere.

- [Putting documents in](#putting-documents-in)
- [Asking](#asking)
- [Manage](#manage)
- [Exporting the graph](#exporting-the-graph)
- [The command line](#the-command-line)
- [The HTTP API](#the-http-api)

## Putting documents in

On the **Ask** tab, drop a folder (or **Choose folder**), or press **Try sample documents**: ten
invented documents of a fictional company — nineteen in the graph, counting the files embedded in
them. Each document is uploaded, read, linked to the others and embedded for search; the bar at the
top shows the progress. Files that are not xlsx, pptx, docx or pdf, hidden files and Office lock
files (`~$…`) are skipped.

Reading calls the model for every page. **Readings per page** (Manage › Keys and model) is 2 in the
app: each page is read twice and read once more only where the two disagree. Set it to 1 to halve the
cost.

A file whose content is already in the graph is not read again (no model cost) — adding the samples
twice reads them once. To force a new reading, use `graphme build-graph <folder> --again`.

**Cancelling a build** (the **Cancel** button on the progress bar) skips the documents not read yet,
keeps the ones already in the graph, and still finishes the search index. Model calls already sent
run to the end; documents read that way appear in the library as "outside the graph", where
**Read again** puts them in at no cost and **Remove from list** drops them.

**Check that it worked:** the top bar shows `19 documents · … links`; asking about `WO-2026-031`
brings the work order first, and its page is drawn on the right; a question about nothing in the
documents gets **No related documents**.

## Asking

A question splits a wide window into three columns (a narrow one shows the middle column only).

**Left — recent questions.** The last ten asked in this tab; a question also lives in the address
(`?q=…&k=…`), so it can be bookmarked or shared, and reopening it searches the graph as it is now.

**Middle, top — the answer.** Written from the documents found, in the question's language (English
when the question has none — only numbers or codes). It starts collapsed to four lines. A `[file
name]` in the answer selects that document below and opens its page on the right. The answer can be
stopped or written again.

**Middle — the documents found, as folders.** The documents whose passages match the question are the
top-level folders (hop 1, numbered by rank); inside each, the documents linked to it (hop 2); inside
those, theirs (hop 3).

- Each row shows its hop, how it relates to the row above (← cited by · → cites · ≈ similar), the
  line that brought it in, and the file's modified date.
- A mark shows a document the answer cites; **↗ n more** marks one that also appears in other
  folders.
- The top three folders start open; a closed folder shows how many it holds (+N). Filter by format
  (DOCX, PPTX…).
- Keyboard: ↑↓ move, → open or go in, ← close or go up, Enter opens.
- **Passages** is how many passages the search starts from. With few documents and a large value,
  everything comes back as hop 1.
- **Download** saves the documents found as one zip with a folder per hop. Pick files one by one, a
  hop at a time, or all; a file that cannot be found is listed in `missing-files.txt`.
- When no passage survives the evidence check, the result is **No related documents** and no answer
  is written (see [retrieval](retrieval.md)).

**Right — the preview.** The path (question › document › linked document), the matching passages one
at a time (‹ ›), and the page each is on; a page holding a match has a dot on its tab. On a narrow
screen this slides in from the side.

## Manage

- **Documents** — everything in the graph, and what is still outside it (being read, failed), with
  each file's state and modified date.
- **Links** — one circle per document. A solid arrow is a citation (citing → cited), a dashed line is
  similar content. Click to outline the linked documents; double-click to open one. The 300 best-linked
  documents are drawn; the rest are reachable from the list and from questions.
- **A document** — its outline graph on the left (radial, tree or Neo4j-style layout, or a list in
  reading order that shows even folded nodes), its rendered pages on the right. Clicking a node shows
  its text, crop and properties in place of the page. Passages are shown as bands behind the nodes:
  the darker, the more important within the document.
- **Citation links** join a document to every document whose name, number or title it writes, all
  versions of a file included.
- **Similarity links** join two documents only when their closest pair of passages, weighted by
  importance, reaches cosine 0.8; at most twenty per document. Documents on the same topic below that
  stay unlinked.
- **Deleting a document** (trash icon, after a confirmation naming it) removes its nodes, passages
  and every citation or similarity link to or from it; other documents' links stay. Not possible
  while a graph is being built. CLI: `graphme delete-document <document_id>...` (asks first; `--yes`
  does not).
- **Danger zone — reset** shows how many documents will go and asks for a confirmation word. It
  empties the graph (indexes stay) and drops what the app had read, so documents added afterwards are
  read from scratch. CLI: `graphme reset-graph`.
- **Keys and model** — the provider, the model, the keys and readings per page. A key is checked
  before it is saved and never shown back. Editing `.env` directly works too; shell variables override
  it.

## Exporting the graph

**Manage › Export** (or `graphme export-corpus`) writes the graph — documents, outlines, links,
passages and their vectors — in three shapes:

| Shape | Command | For |
|---|---|---|
| Import bundle | `graphme export-corpus OUT_DIR` | your own Neo4j: run `./import.sh` against a stopped, empty database, start it, run `schema.cypher` once |
| `.dump` | `graphme export-corpus graph.dump --dump` | Neo4j Desktop, `neo4j-admin database load`, Aura's *Import Database* |
| Aura upload | Manage › Export | replaces an Aura instance's data (asks for the instance, user and password, and a confirmation that the data leaves your machine) |

The bundle holds one CSV per label set and per relationship type, the passages (`Chunk`) with their
vectors, `schema.cypher` (constraints, the vector index and the full-text index), `import.sh` and a
`README.md`. Loaded into an empty Neo4j, neo4j-graphrag's `VectorRetriever`, `HybridRetriever`,
`HybridCypherRetriever` and `GraphRAG` work on it with no code from here. `--json` writes the same
records as one file; `--cypher` writes the graph without passages or vectors (vectors in a Cypher
script make the server parse every number and run out of memory).

A `.dump` is made by the app's own `neo4j-admin`: the bundle is imported into a scratch database, a
scratch Neo4j makes its indexes, and the database is dumped. The app's graph is never touched.

## The command line

The CLI works on the same graph as the app. In a folder without a `.env`, it uses the installed app's
settings, graph and viewer.

```text
graphme build-graph FOLDER          # read, put in the graph, link, embed (--again: read again; --no-embed)
graphme embed-graph                 # embed again (after --no-embed, or a new embedding model)
graphme ask QUESTION                # the answer and its sources
graphme delete-document ID...       # delete documents
graphme reset-graph                 # empty the graph (indexes stay)
graphme export-corpus OUT           # import bundle (--dump / --json / --cypher)
graphme xlsx-bundle WORKBOOK OUT    # draw one workbook into pages and their cell mapping
graphme serve                       # the HTTP API and the web app
graphme app                         # the app in a browser tab
```

## The HTTP API

`graphme serve` opens the same pipeline and graph at `/v1`; the request and response shapes are at
`http://127.0.0.1:8765/docs`, and the web app is served at `/` when it is built.

```bash
B=http://127.0.0.1:8765
curl -s $B/health                                                   # never touches the graph DB
id=$(curl -s -F file=@memo.docx $B/v1/files | jq -r .id)            # keep a file on the server
job=$(curl -s -X POST $B/v1/jobs/build -H 'content-type: application/json' \
  -d "{\"files\": [\"$id\"]}" | jq -r .id)                          # 202: read → put → link → embed
curl -s $B/v1/jobs/$job | jq .state                                 # reading … done
curl -s -X POST $B/v1/ask -H 'content-type: application/json' -d '{"question": "What is the test pressure?"}'
curl -s -o bundle.zip $B/v1/export/bundle                           # the import bundle
```

- Long work (`jobs/build`, `jobs/read`, `jobs/embed`, `jobs/dump`, `jobs/aura-upload`) answers 202
  with a job id to poll at `GET /v1/jobs/{id}`. Building, reading and embedding share one slot and
  exporting has another; a busy slot answers 409. A build or a reading stops with
  `POST /v1/jobs/{id}/cancel`. Jobs live in memory; the graph and uploaded files stay in
  `GRAPHME_DATA_DIR/server`.
- An error body is always `{"detail": "..."}`.
- With `GRAPHME_API_KEY` set, all of `/v1` asks for `Authorization: Bearer <key>` (or the
  `graphme_key` cookie the web app sets after asking for the key). `/health`, `/docs` and the web
  app's files need no key.
- Without a key the server opens on 127.0.0.1 only and answers this machine's pages only.
- A build from `{"folder": "<path>"}` reads that folder with the server's permissions: the key is
  that permission.
- `PUT /v1/settings` (the Keys and model page) is taken only from this machine's page, key or not.
