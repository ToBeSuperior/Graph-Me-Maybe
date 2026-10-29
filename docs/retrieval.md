# Retrieval

A question runs two searches side by side and fuses their rankings. Both run on Neo4j indexes, and
the fusion runs inside Neo4j too.

| Step | What happens |
|---|---|
| 1. Vector search (ANN) | The question is embedded and the vector index (HNSW) finds passages close in meaning. |
| 2. Full-text search (BM25) | The full-text index finds passages with the question's words — what has to match letter for letter, like a document or part number. Korean is cut into two-character pieces by the `cjk` analyzer, so a word matches whatever particle follows it. |
| 3. Weighted RRF | Each search returns up to 400 candidates (`SEARCH_DEPTH`), however many documents are asked for. A passage at rank r gets `weight / (k + r)` from each search, summed; a passage found by only one search still ranks. |
| 4. No related documents | A rank says nothing about relevance — every question has a first place — so only passages with evidence are fused: cosine with the question at least `GRAPHME_RELEVANCE_FLOOR` (measured per model on the invented samples: 0.39 for the general model run on this machine, 0.46 for the Korean one, 0.2 for text-embedding-3-large), or holding every word of the question, or the document number it names. A one-word question still finds the passages holding that word; a question sharing a few words by chance usually finds nothing; and where something passes the floor without establishing what the question asks about, the answer says it cannot answer. Nothing left means "no related documents". |
| 5. A document named first | When the question names a document -- by its number, its file name or its title -- and the search found it, that document's passages go first, in their own order. The model splitting the question also copies the phrases that name a document; a document number is seen without it. A phrase names a document by the rules a page's mention of another document is judged by, or by being held whole in the file name or title of exactly one document. |
| 6. Gather documents | A question asks for documents (5, 10, 15 or 20 in the app). The passages left are walked best first until that many documents are reached, the passage reaching the last one included; a document gives at most 5 passages, and the walk goes on past the rest of its own. Asking for fewer documents takes the front of the same walk. |
| 7. Walk the graph | From each document found -- once a document, from its best passage -- follow citations (both ways) and similarity links to hop-2 and hop-3 documents, and bring their passages nearest to the question. A linked document comes only with passages that reach the same relevance floor; one with none is left out, and so is what it links to. |
| 8. Answer | The page shows every document found. The answer reads the passages in the same order, each with its linked documents, as far as 12,000 tokens go (`ANSWER_TOKENS`) -- the first always. |

| Setting | Default | Why |
|---|---|---|
| `GRAPHME_RRF_K` | 60 | the value Cormack et al. (SIGIR 2009) fixed, the standard default since |
| `GRAPHME_HYBRID_DENSE_WEIGHT` | 0.6 (BM25 0.4) | close to even, since weighted RRF rarely beats unweighted without tuning (Bruch et al., TOIS 2023), leaning to vectors, which serve general questions better |
| A question naming a document number | 0.4 (BM25 0.6) | full-text serves specific questions better; per-question dynamic weighting (arXiv 2504.05324) |

The weights are starting points that follow the literature; tune them with questions about your own
documents. neo4j-graphrag's hybrid retriever only offers score normalisation (naive and linear), so
RRF is this repository's own query. A graph exported to another Neo4j and searched with
neo4j-graphrag's default retrievers may therefore rank slightly differently.

Passages are cut from each document's outline: a section that fits is one passage, a larger one is
cut between its items under its headings, and a single unit too big is cut between its lines. Each
passage carries an importance score, used by similarity links and shown in the document view. The
details, and why it is done this way, are in [method.md](method.md).
