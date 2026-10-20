# Graph-Me-Maybe — What Is Left to Do

> Updated: 2026-10-15
>
> Finished work is not listed here. Confirmed behavior is in
> [`../finalized_source.md`](../finalized_source.md), and the history is in
> [`method.md`](method.md).

## Current state in one line

Extraction → browser render → unit crop → VLM hierarchy → graph space → question runs
end to end, and the VLM hierarchy handles worksheets, slides, DOCX and PDF.

## 0 · Next plan (2026-10-15)

- [ ] **Make the open-source release easier to use with per-feature APIs.** Expose reading
      (extraction, rendering, VLM hierarchy), graph loading, search, question answering,
      document linking and export as separate APIs. Today the demo server
      (`tools/hierarchy_demo/app.py`) ties this flow into one block, so another project
      cannot easily take just part of it.
- [ ] **Measure efficiency and performance on the latest GraphRAG benchmarks.** Record
      ingest time, model call count and tokens, and query latency, not only accuracy.
      Compare with other GraphRAG implementations under the same conditions.
- [ ] **Ship the demo with an installer.** Let people start the demo from one installer,
      without installing dependencies such as Neo4j, Chrome and Node by hand.

## 1 · Wire the hierarchy into production indexing

- [ ] Index the whole corpus once in `derived` mode and measure how hierarchy edges change
      search results. So far it has only been run in the demo.
- [ ] Decide from per-page call cost and latency whether the default is `off` or whether to
      turn it on by format and size. Put the reading count (`GRAPHME_HIERARCHY_READINGS`) on
      the same scale — with 2 readings the calls double, and only pages where the readings
      differ cost one more.
- [ ] Decide whether to record the places where readings differ. Today which reading was
      chosen and why stays in the log only, not in the graph.
- [ ] Check the page-carry rule for flowing documents on longer documents. Today, within a
      budget of 1,200 tokens, it carries the open chain → the heading skeleton (up to half
      the budget) → the recent section whole, and sends the bottom 30% image along. Check
      whether it is acceptable that deep headings drop first when the skeleton exceeds the
      budget in documents of dozens of pages.
- [ ] Decide whether to use text grouping for DOCX too. Today it is PDF and PPTX only
      (`GROUPING_FORMATS`). We judged DOCX does not need it because the file gives whole
      paragraphs, but we have not checked headings split across text boxes.
- [ ] Check the small-print rule (difference from the modal body size, presence of body text
      above and below) and the number of pieces per call on more document types. The rule was
      tuned on only two kinds, papers and Korean reports.
- [ ] Decide whether to absorb text inside the frame of a split table even when figure
      grouping did not group the table. Today it is absorbed only when the page says it is one
      table.
- [ ] Decide whether to crop a table that spans pages on both pages. Today it is cut once
      from the side where more of it is visible, so the piece on the other side is not in the
      crop.
- [ ] Decide whether to count leading spaces before a sentence in PDF too. Today `indent`
      counts both ways (paragraph settings and leading spaces) only for DOCX, and PDF uses
      only the block's left coordinate.
- [ ] Check the frame-detection thresholds (area 15%, fill 50%, longest cell 200 characters)
      on other PDFs. They are values tuned on only two documents.
- [ ] Load the Neo4j export into a real instance and check it. So far only the shape of the
      scripts and records has been verified (there is no local Neo4j). Also decide whether to
      give top-level units an `OUTLINE_PARENT` from the page root, so that the hierarchy
      relations alone form a complete tree.

## 1.5 · Search and the web app

- [ ] (Refinement) A question in one language finds little in documents written in another:
      an English question over Korean documents left them below the relevance floor, which
      was measured on English questions over English documents. One option is for the
      question split (`SPLIT_SYSTEM`) to also write each query in the language of the
      documents it searches.
- [ ] In a question that uses a document number together with a common word such as
      "document summary", the document with that number is pushed down from the top. When a
      number is present, the full-text side gets more weight, but the common word matches
      passages in other documents. Decide whether to boost the document whose number matches
      or to drop the common word from the full-text query.
- [ ] One document sometimes takes over the first-round results. Decide whether to limit the
      number of passages per document.
- [ ] Tune RRF `k` (60), the weight (0.6) and the floor (0.3) on a real question set. Today
      they are set from literature values and sample documents only.
- [ ] Some pages keep watermarks and repeated header elements without `drop`. Since we chose
      not to use a rule filter (method.md), we reduce them through the context the model sees
      and the prompt.
- [ ] The "N / total" in the progress display is the number of documents put into the graph.
      Reading runs in parallel, so the number looks stuck until a long document earlier in
      the order finishes. Decide whether to show the number read separately.
- [ ] Changing the passage count asks the question again without confirmation. A citation
      chip in the answer does not jump to the evidence passage.

## 2 · Breaking down tables

- [ ] Decide whether to split a table into cell, row and column semantic units, and if so,
      set the rules. Today one table is one node. This item is on hold until the
      representative table meaning and the ownership rules are settled.
- [ ] (Refinement) A worksheet table drawn without borders is not recognised as a table: each
      cell becomes a text unit of its own, and the outline pass hangs every value under the
      first cell or header, flat, so its rows and columns are lost. A table needs its border
      today (`extractors/xlsx_visual_tables.py`). Decide how a borderless table on a sheet is
      told from loose cells -- by principle, for any workbook, not by the shape of the ones
      at hand.

## 3 · Quality evaluation set

- [ ] Extend the synthetic fixtures per format and build a small representative set with the
      expected source mapping, information units, relations and evidence.
- [ ] Add a golden evaluation that measures mapping and layout fidelity, split and relation
      quality, evidence recall, citation resolution and answer quality **separately**.
- [ ] Write down the limits and the non-claims together. Do not use confidential material
      without data-handling approval.

## 4 · Live provider smoke test — approval required

- [ ] Get explicit operational approval before any provider-backed run.
- [ ] Check that the model endpoint points where intended (`model_endpoint.py`), and use only
      newly made synthetic, non-confidential documents.
- [ ] Run `build-graph → embed-graph → ask` and record the sanitized results — readiness,
      structure counts, citation resolution, provider and model identifiers. Do not record
      secrets or raw provider content.
- [ ] Do not mix an embedding-only success into claims about VLM reading or question quality.

## 5 · Extending browser automation

- [ ] Extend the automation beyond the current graph, evidence, table, caption and
      logical-component flows to upload, query, and readiness and error states.

## 6 · Reconciling documents

- [ ] Align the README onboarding with the real CLI and web surface.

Diagrams are no longer maintained. The pipeline is covered by the stage table in
`finalized_source.md` and the final code list, and keeping a second copy of the same content
as a picture let it drift while nobody was looking — a picture you cannot trust is worse than
no picture. If one is needed, draw a fresh one then.

## 7 · Remaining technical debt

- [ ] `tools/hierarchy_demo/` is excluded from mypy. Renaming a field breaks it silently (this
      actually happened with the `bbox` → `crop` change). Decide whether to include it.
- [x] Removed the traces of the marker-based hierarchy (`application/hierarchy.py`,
      `hierarchy_reconciliation.py`, `hierarchy_benchmark.py`, `hierarchy_features.py`,
      `hierarchy_prompt_evaluation.py`) and the unconnected caption modules
      (`caption_generation.py`, `caption_validation.py`). The `derived` mode and
      `visual_captions.py` do the same job.
- [ ] The demo keeps document results only in server memory, so a restart starts again from
      scratch.
- [ ] Decoding metafiles (EMF, WMF) relies on a viewer patch. 16-bit DIB support was added,
      but compressed DIBs (`BI_JPEG`, `BI_PNG`) and `BI_BITFIELDS` are still not drawn — add
      them in the same place if they show up in a real document. A whole picture can vanish
      without an error, so the symptom is silent.
- [ ] The worker's copy of the metafile player is still unpatched. If rendering moves to the
      worker, the same branch must go in there too (noted in `viewer/README.md`).
- [ ] (Refinement) A unit the figure pass wrongly calls part of a figure is never shown to the
      outline pass again, on any format: its place comes from `figure_part_decisions`
      alone. Worksheets now follow the same rule as slides and pages
      (`build_placed_scope`, pictures and charts placed by their DrawingML extent, text and
      tables by their cells). If a misjudged part turns up, the correction belongs in the
      figure pass or in binding, not in a format-specific exception.
- [ ] Settle the git baseline (initial files and ignore policy) under the owner's approval.
      Do not commit or push it as part of roadmap verification.

## Operating constraints

- Do not call a provider without approval.
- Do not print, copy or commit `.env`, provider API keys or their metadata.
- A fake in the test profile is not live verification.
- When only documents change, run only the markdown and link checks and `git diff --check`.
  Run Ruff, mypy and the tests only when source is changed with them.
