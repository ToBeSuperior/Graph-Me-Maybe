<div align="center">

# Graph-Me-Maybe

**Turn a folder of PDF, Word, PowerPoint and Excel files into one knowledge graph, and ask it questions.**

[![License: PolyForm Noncommercial](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)](LICENSE.md)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Neo4j 5.26](https://img.shields.io/badge/Neo4j-5.26-018bff)](https://neo4j.com/)
![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)

[Install](#-install-the-app) ·
[Usage](#-usage) ·
[How it works](#-how-it-works) ·
[Configuration](#-configuration) ·
[Develop](#-develop-from-source) ·
[Contribute](CONTRIBUTING.md)

</div>

---

Graph-Me-Maybe is a local-first app. It reads each document the way a person would — as rendered
pages — works out what sits under what, links documents that cite or resemble each other, and stores
all of it in a single Neo4j graph. A question is answered from that graph with its sources: the
documents that match, and the documents linked to them.

Your documents, the graph and your keys stay on your machine. Only the model and embedding calls
leave it, to the provider you choose.

## ✨ Features

- **Four formats, one graph.** PDF, DOCX, PPTX and XLSX, including files embedded inside other files
  (a workbook in a deck, a PDF in a memo).
- **Structure read off the page.** A vision-language model reads each rendered page and decides the
  outline — headings, sections, what a table or a figure belongs to — instead of trusting styles or
  font sizes.
- **Documents linked to each other.** A document that names another by number or title is linked to
  it; documents with similar content are linked too. Answers follow those links.
- **Hybrid retrieval.** Vector search and full-text search (BM25) run side by side in Neo4j and are
  fused with weighted reciprocal rank fusion. A question about nothing in the graph gets "no related
  documents", not a guess.
- **Search on this machine.** Passages are embedded by a small model run locally (a general one by
  default, or one for Korean), so searching needs no key and sends nothing out. OpenAI or Gemini
  embeddings remain a choice.
- **Your cloud drives.** Box Drive, Google Drive for desktop and OneDrive (with the SharePoint
  libraries it syncs) are browsed in the app as folders and built from; Box can also be signed in to
  directly. A file kept online only is given back to the cloud once read, so a large folder does not
  fill the disk.
- **Workspaces.** Graphs kept apart, each with its own documents and links, switched in seconds.
- **A desktop app.** One-line installers for macOS and Windows; the graph database (Neo4j) is
  installed and run by the app itself, with no Docker and no account.
- **Open graph.** Export to a `neo4j-admin` import bundle, a `.dump` for Neo4j Desktop or Aura, or
  upload straight to Aura — Neo4j's own GraphRAG retrievers work on it with no code from here.
- **CLI, Python API and HTTP API** over the same pipeline and the same graph.

## 📦 Install the app

Paste one line into a terminal. It installs the app and what it needs (Python, the web app, the
document viewer, a browser to draw pages with, Neo4j and Java), then opens the app. Run it again to
update.

**macOS and Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.sh | sh
```

**Windows** (PowerShell)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.ps1 | iex"
```

Then open **Graph-Me-Maybe** from Applications (macOS) or the Start menu (Windows), and put your
model key in **Manage › Keys and model**. Try it with **Try sample documents** on the Ask tab.

> [!NOTE]
> On Windows, install from PowerShell, not inside WSL. Updating, uninstalling, where the app keeps
> its files, and troubleshooting are in [docs/installation.md](docs/installation.md).

## 🚀 Usage

### The app

Drop a folder of documents on the **Ask** tab (or use the sample documents), wait for it to be read,
and ask a question. The answer cites its documents; the documents found are shown as folders by how
they were reached — found by the question, linked to those, linked to those in turn — with the
matching passage highlighted on the rendered page. **Manage** holds the document list, the map of
links, each document's outline, the cloud drives, export, keys and model, and the danger zone; the
last build's result (how many went in, and why each failure failed) stays in its side menu. The
workspace open is named in the top bar, where another is opened or made. A tour of every screen is in
[docs/user-guide.md](docs/user-guide.md).

### Command line

The CLI uses the same graph as the app.

```bash
graphme build-graph ./docs            # read a folder, put it in the graph, link and embed it
graphme ask "What pressure does the leak test use?"
graphme export-corpus ./out --dump    # one .dump file for Neo4j Desktop or Aura
graphme --help                        # every command
```

### Python

```python
import graphme

doc = graphme.read("report.pptx")        # one document -> ExtractedDocument (no Neo4j needed)

with graphme.GraphMe() as g:              # settings from .env / the environment
    g.add("docs/")                        # a folder or one file -> BuildReport
    g.embed()                             # cut passages and embed them for search
    for hit in g.search("leak test pressure"):
        print(graphme.shown_name(hit.file), hit.score)
    print(g.ask("What pressure does the leak test use?").text)
    g.export("out/")                      # neo4j-admin import bundle (dump=True for a .dump)
```

The public API is exactly the names in `graphme.__all__`. More in [`examples/`](examples/).

### HTTP API

```bash
graphme serve                          # http://127.0.0.1:8765 — the API, its docs at /docs, the web app
curl -s -X POST http://127.0.0.1:8765/v1/ask -H 'content-type: application/json' \
  -d '{"question": "What pressure does the leak test use?"}'
```

Long work (reading, embedding, exporting) runs as jobs you poll at `GET /v1/jobs/{id}`; every error
body is `{"detail": "..."}`. Without `GRAPHME_API_KEY` the server answers this machine only. The
full contract is at `/docs` and in [`src/graphme/server/models.py`](src/graphme/server/models.py).

## 🧠 How it works

```text
  files ──▶ extract ──▶ render pages ──▶ bind visuals ──▶ VLM: figures, paragraphs, outline
                                                                        │
  answer ◀── walk links (2-3 hops) ◀── hybrid search ◀── passages ◀── one Neo4j graph
                                       (vector + BM25,                  (documents, outlines,
                                        weighted RRF)                    citations, similarity)
```

1. **Extract.** Text, tables and pictures come straight from the file. The file owns the graph:
   what the model sees later can add evidence, never invent nodes.
2. **Render and bind.** Each page is drawn (Office files in a browser, PDFs rasterised) and every
   unit is placed on it, so a table or a picture has an exact crop.
3. **Read the page.** A vision-language model is asked, page by page, which units form one figure,
   which pieces form one paragraph, and what reads under what. Long documents carry their outline
   from page to page.
4. **Link.** A document that names another is linked to it; documents with similar passages are
   linked too.
5. **Retrieve.** The outline is cut into passages and embedded. A question runs vector and full-text
   search in Neo4j, fuses them, keeps only passages with real evidence, then walks the links to bring
   the connected documents along — see [docs/retrieval.md](docs/retrieval.md).

The rules are in [`finalized_source.md`](finalized_source.md); why the pipeline looks like this, and
what failed on the way, is in [`docs/method.md`](docs/method.md).

## 📄 Supported formats

| Format | Read | Drawn by | Notes |
|---|---|---|---|
| PDF | text, tables, images, attachments | PyMuPDF | scanned (image-only) pages are flagged; OCR text is not promoted into the graph |
| DOCX | paragraphs, tables, pictures, embedded files | browser viewer (WASM) | headers, footers and comments are not extracted |
| PPTX | slides, shapes, tables, charts, pictures, embedded files | browser viewer (WASM) | slides keep their order |
| XLSX | sheets, tables, charts, pictures, formulas, embedded files | browser viewer (WASM) | formulas are linked, not recalculated |

## ⚙️ Configuration

Settings come from defaults, then `.env`, then a YAML file named by `GRAPHME_CONFIG` (never secrets),
then the environment. The installed app keeps its `.env` in its own folder; the app's
**Manage › Keys and model** page writes it for you. Every setting is listed with comments in
[`.env.example`](.env.example).

| Setting | What it is |
|---|---|
| `GRAPHME_PROVIDER` | who reads documents and answers: `openai` (default), `anthropic` or `gemini` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | that provider's key |
| `GRAPHME_EMBEDDING_PROVIDER` | who embeds passages for search: `general` (default) or `korean`, run on this machine with no key; or `openai` / `gemini` with that provider's key |
| `GRAPHME_NEO4J_URI` (+ `GRAPHME_NEO4J_PASSWORD`) | use a Neo4j you run yourself (Desktop, a server, Aura) instead of the app's own |
| `GRAPHME_HOME` | the app's folder (settings, graph, viewer) |
| `GRAPHME_RETRIEVAL_TOP_K` | how many passages a question starts from |
| `GRAPHME_HIERARCHY_READINGS` | how many times each page is read (more readings, steadier outlines, more cost) |
| `GRAPHME_NEO4J_HEAP` / `GRAPHME_NEO4J_PAGECACHE` | memory of the app's Neo4j, for large graphs |
| `GRAPHME_API_KEY` | required by `graphme serve` before it opens beyond this machine |
| `GRAPHME_BOX_CLIENT_ID` / `GRAPHME_BOX_CLIENT_SECRET` | your own Box app, to sign in to Box directly (Box Drive needs none) |

## 🛠 Develop from source

**Requirements:** Python 3.11+ and [uv](https://docs.astral.sh/uv/) for everything Python; Node.js 24+
(npm 11+) for the web app; Google Chrome (or Playwright's Chromium) and, once, Rust with
`wasm32-unknown-unknown` plus [`wasm-pack`](https://github.com/rustwasm/wasm-pack) to build the
document viewer. Running only the tests needs Python and uv alone.

```bash
git clone https://github.com/ToBeSuperior/Graph-Me-Maybe.git
cd Graph-Me-Maybe
uv sync --extra dev                               # Python 3.11 and the dependencies
(cd frontend && npm ci && npm run build)          # the web app
cp .env.example .env                              # your settings (never copy another machine's)
src/graphme/adapters/rendering/viewer/setup.sh   # the document viewer, once (~1-2 min)
```

Put your provider's key in `.env` (see [Configuration](#-configuration)). To use it only as a
library, `pip install .` in the clone installs the CLI, the Python API and the server.

Run the app with the sample documents, or the bare server:

```bash
uv run python tools/hierarchy_demo/app.py     # http://127.0.0.1:8765, with "Try sample documents"
uv run graphme serve                          # the same server without the samples
(cd frontend && npm run dev)                  # http://127.0.0.1:5173, hot reload, /v1 proxied to 8765
```

The first start fetches Neo4j and Java into the app folder (about 210 MB) and starts them; with
`GRAPHME_NEO4J_URI` in `.env` it uses that Neo4j instead.

**Checks** — what CI runs, and what a pull request has to pass:

```bash
uv run ruff check src tests/public && uv run mypy
uv run pytest tests/public                     # no key, no Neo4j, no real document needed
cd frontend && npm run typecheck && npm test && npm run e2e && npm run build
```

## 🤝 Contributing

Contributions are welcome — bug reports, fixes, new format support, documentation. Start with
[CONTRIBUTING.md](CONTRIBUTING.md): it walks through the setup, the layout of the code, how tests are
written here (invented data only, a failing test first), and how a pull request is reviewed. Please
also read the [Code of Conduct](CODE_OF_CONDUCT.md); security issues go to
[SECURITY.md](SECURITY.md), not to public issues.

## ⚠️ Known limitations

- OpenAI is the only provider checked against live calls; the Anthropic and Gemini paths are covered
  by recorded-response tests only.
- Office files are drawn by a browser-based viewer, so DOCX pagination and PPTX effects are close to,
  not pixel-identical with, Word and PowerPoint.
- Formulas are linked, not recalculated; Excel's calculation engine is not replaced.
- The outline is read page by page by a model; a page the model gives no usable answer for stays
  flat and is reported as `hierarchy_page_unread`.
- Mixed-column layouts and borderless tables in PDFs are read on a best-effort basis.
- The `.dump` export is checked by loading it into an empty Neo4j 5.26; Aura upload has not been
  checked end to end.
- Giving a cloud file back after it is read works on macOS (Box Drive, Google Drive, OneDrive) and,
  on Windows, for OneDrive only; OneDrive/SharePoint has been checked with invented folders only.

## 🔒 Security and data

- Never commit `.env`, real documents, API keys or OAuth files.
- Open `graphme serve` beyond this machine only with `GRAPHME_API_KEY` set; without one it stays on
  127.0.0.1.
- Check your model provider's data terms before sending confidential documents to it -- and the
  embedding provider's, if you choose OpenAI or Gemini for search (the default runs on this machine).
- Generated captions are kept apart from what the file says and are never used as a citation.

## 📜 License

[PolyForm Noncommercial 1.0.0](LICENSE.md): free for personal study, research, education,
non-profit organisations and public institutions to use, change and share; not for commercial use or
use in a company's business. The bundled document viewer is MIT-licensed; Neo4j and Java are fetched
from their publishers under their own licenses — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
