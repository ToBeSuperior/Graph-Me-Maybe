# Greedy roadmap (2026-11-15)

The owner's answers of 2026-11-15 to the list of what was left for them, taken in order. Each
item is ticked when done; an item that needs the owner says so and what it waits on.

Standing rules for this list:

- **Real provider APIs are tested last**, after everything else here (OpenAI, Anthropic, Gemini
  with real keys). Until then: the dev gateway, fakes, invented data.
- Anything done on the owner's computer through computer use, or in their browser through
  Claude in Chrome, is asked first (a notification), then done.
- Items 10 (conversation turns), 11 (search only when needed) and 13 (team workspace) of the
  earlier list stay out.

## 1 · Decisions

- [x] **1.1 Document number rule** -- rule B chosen and built 2026-11-15 (f7a0d2f).
      -- explained again with examples; the owner decides.
      Held as a TODO in `docs/roadmap.md` §3.
- [x] **1.2 Searching several workspaces** -- approved and built 2026-11-15 (plan tasks 1-5;
      moved the dev home's graphs, golden unchanged, roadmap §search timing) -- decided by the owner:
      1. One Neo4j holds N graphs, one per workspace, all available at once.
      2. A search runs only inside the graph its workspace is given -- never across.
      3. A search over several workspaces asks each graph on its own, gathers the rankings and
         reranks them into one list.
      Design: `docs/superpowers/specs/2026-11-15-workspaces-in-one-graph-db-design.md` -- approved 2026-11-15.
      Constraint to settle first: Neo4j Community (5.26, what the app ships) holds one user
      database at a time; several databases need Enterprise. Ways under Community: one database
      with every node and passage tagged by workspace, each search held to its tag (vector and
      fulltext search filtered by it). Design doc first, then build.
- [x] **1.3 The Google client in the public build** -- check whether shipping its id and secret
      lets anyone abuse it, and whether encrypting it helps. Reported 2026-11-15: a desktop client's
      secret is not secret by Google's own terms; encryption cannot hide it; ship it, PKCE kept.
- [x] **1.4 Tables** -- a matter of method, left as a TODO for a fuller discussion
      (`docs/roadmap.md`). Simpler items first.

## 2 · Accounts (the owner does them; steps written for them)

- [x] **2.1 Microsoft publisher verification** -- steps given to the owner 2026-11-15; waits on them.
- [x] **2.2 PyPI** -- registering the project and giving release rights (trusted publishing from
      GitHub Actions) -- steps given to the owner 2026-11-15; waits on them.

## 3 · Checks in the real world

- [ ] **3.1 Outlook with a real mailbox** -- the owner signs in; then the reading, the counts,
      the messages and attachments are checked here.
      2026-11-15: signed in with a Microsoft account made on a Naver address -- connected, 0
      messages, since its Outlook.com mailbox is empty (mail to that address goes to Naver). Needs
      a mailbox that holds mail. Also: say "this mailbox is empty" on the Mail page rather than
      showing nothing.
- [ ] **3.2 Real cloud drives** -- what each needs first (Google Drive for desktop, a shared
      folder, OneDrive, Box Drive, Dropbox), installed through computer use, each step asked
      first; then a build from each.
- [ ] **3.3 Real provider APIs** -- last of all (see the rules above).

## 4 · The Mac

- [x] **4.1 A request to whoever has the Mac**: bump the viewer (rhwp/docx tags), rebuild with
      Rust and wasm-pack, check, commit. Written as `docs/requests/mac-viewer-bump.md`.

## 5 · Material for measuring

- [ ] **5.1 Public benchmarks and open data** for item 17 (refinements measured on real
      documents): search for document-QA sets with spreadsheets, slides, Word and PDF, with
      licences that allow use; pick, fetch, and wire them into `tools/golden` or a sibling.
      Shortlist (2026-11-15, licences as their cards state; download waits on the owner):
      1. Microsoft OfficeComprehensionBenchmark (native docx/xlsx/pptx, cross-document
         questions, CDLA-Permissive-2.0) -- mapping, answers, links.
      2. SpreadsheetBench (native xlsx, answer cell positions, CC BY-SA 4.0) -- the right cell.
      3. allganize RAG-Evaluation-Dataset-KO (Korean, 300 questions, file + page, MIT; PDFs under
         their sources' terms) -- Korean retrieval.
      4. KoViDoRe v2 (Korean public reports, page relevance, annotations CC BY 4.0).
      5. ViDoRe v3 (PDFs with evidence boxes, annotations CC BY 4.0) -- citation place.
      No public HWP/HWPX QA set exists; one would be made from KOGL type-1 files on data.go.kr.
      Not usable in the public package: SlideVQA (NTT evaluation-only licence), SheetCopilot
      (GPL, non-commercial).

## 6 · Out

- Items 10, 11, 13 stay out.
