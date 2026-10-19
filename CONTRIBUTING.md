# Contributing

Thank you for helping. Bug reports, fixes, documentation and new features are all welcome. This guide
gets you from a fresh clone to a pull request; everything in it runs without an API key, a Neo4j or a
real document.

- [Ways to contribute](#ways-to-contribute)
- [Set up](#set-up)
- [Find your way around](#find-your-way-around)
- [Tests](#tests)
- [Rules that keep the project safe](#rules-that-keep-the-project-safe)
- [Style](#style)
- [Pull requests](#pull-requests)

## Ways to contribute

- **Report a bug** with the [bug form](../../issues/new?template=bug_report.yml). Reproduce it with an
  *invented* document if you can — never attach a real one.
- **Suggest a feature** with the [feature form](../../issues/new?template=feature_request.yml).
- **Improve the docs.** Anything unclear in the README or `docs/` is a bug too.
- **Fix something.** Issues labelled `good first issue` are a good start; comment on one before you
  begin, so two people do not work on the same thing.

Security problems go to [SECURITY.md](SECURITY.md), not to public issues. Everyone taking part follows
the [Code of Conduct](CODE_OF_CONDUCT.md).

## Set up

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/). That is enough for the checks below.

```bash
git clone https://github.com/ToBeSuperior/Graph-Me-Maybe.git
cd Graph-Me-Maybe
uv sync --extra dev
uv run pytest tests/public                          # should pass before you change anything
uv tool install pre-commit && pre-commit install    # runs ruff on every commit
```

Working on the web app needs Node.js 24+; drawing DOCX, PPTX and XLSX pages needs Chrome and a
one-time viewer build; reading real documents needs a model key. The README's
[Develop from source](README.md#-develop-from-source) section covers all of it. The graph database
needs nothing: the app fetches and runs its own Neo4j.

## Find your way around

The code is ports and adapters: `domain/` holds the data model, `ports/` declares interfaces,
`adapters/` implements them, `application/` orchestrates, and `runtime.py` is the only place that wires
concrete adapters together. Both the CLI and the server go through it.

| To change… | Look in |
|---|---|
| how a format is read (text, tables, pictures, embedded files) | `src/graphme/adapters/extractors/` (`pdf.py`, `docx.py`, `powerpoint.py`, `excel.py`) |
| how pages are drawn | `src/graphme/adapters/rendering/` (`browser_office.py`, `pdf_pages.py`) and `viewer/` |
| what the model is asked, page by page | `src/graphme/adapters/vision/` and `src/graphme/application/` (`page_hierarchy_scope.py`, `unit_grouping.py`) |
| the order of the whole pipeline | `src/graphme/application/document_graph.py` |
| links between documents | `src/graphme/application/corpus_links.py`, `document_references.py` |
| passages, search and answers | `src/graphme/application/graph_chunks.py`, `src/graphme/adapters/storage/graph_rag.py` |
| the graph database and exports | `src/graphme/adapters/storage/neo4j_space.py`, `local_neo4j.py`, `src/graphme/adapters/export/` |
| model providers | `src/graphme/model_endpoint.py`, `src/graphme/adapters/chat.py` |
| the HTTP API | `src/graphme/server/` (shapes in `models.py`) |
| the CLI and the Python API | `src/graphme/cli.py`, `src/graphme/api.py` |
| the web app | `frontend/src/` (words for both languages in `lib/words.ts`) |
| the installers | `install.sh`, `install.ps1`, `src/graphme/launcher.py` |

Before changing the pipeline, read [`finalized_source.md`](finalized_source.md) (the rules) and
[`docs/method.md`](docs/method.md) (why they are what they are). Many rules there exist because the
obvious approach failed on a real document.

The API contract lives in `src/graphme/server/models.py`. After changing it, run `npm run api` in
`frontend/` to regenerate `frontend/src/api/schema.d.ts`; the web app compiles against those types.

## Tests

```bash
uv run ruff check src tests/public && uv run mypy src                # lint and types (strict)
uv run pytest tests/public                            # the test suite
uv run pytest tests/public/test_read.py -q            # one file
cd frontend && npm run typecheck && npm test && npm run e2e && npm run build
```

CI runs the same commands on Linux and Windows, plus a real install on macOS and Windows.

- `tests/public/` is the suite; `tests/fakes/` holds the invented documents and helpers it uses.
  Documents are built inside the tests with PyMuPDF, python-docx, openpyxl and python-pptx (see
  `tests/public/test_read.py`), and the model is answered by `httpx.MockTransport` — nothing calls a
  real provider.
- The web app's end-to-end tests run against a fake API (`frontend/e2e/fake-api.ts`) with invented
  data.
- **Write the failing test first.** A change in behaviour comes with a test that fails before the
  change and passes after it — and fails because the behaviour differs, not because a name is missing.
- A test that needs a running Neo4j runs only with `GRAPHME_NEO4J_TEST=1`.

## Rules that keep the project safe

- **Invented data only.** Never commit a real document, or anything measured from one — page counts,
  coordinates, sheet names, cell ranges, file names, people's names — in code, tests, comments, commit
  messages or pull request descriptions.
- **No secrets.** Never commit `.env`, API keys or OAuth files. If a model call is needed to check
  something, use your own key and describe what you checked; do not paste model responses or document
  content.
- **The file owns the graph.** What the model or the rendered page says may add evidence to nodes
  extracted from the file; it never creates nodes of its own.
- OpenAI is the only provider checked against live calls. If you add or change a provider, say how
  you checked it.

## Style

- Python: ruff (line length 100) and mypy in strict mode must pass. Match the surrounding code: small
  functions, plain names, and comments that say *why*, not *what*.
- TypeScript: the existing patterns in `frontend/src` (TanStack Query for server state, words in
  `lib/words.ts` for both languages — add every new string in English and Korean).
- Documentation and messages are in English.
- Keep a change focused. A refactor and a behaviour change are easier to review as two pull requests.

## Pull requests

1. Fork, create a branch, make the change with its test.
2. Run the checks above.
3. Open the pull request and fill in the template: what changed, why, and how you checked it.
4. A maintainer reviews it. Changes are integrated by the maintainers (the public repository is
   generated from the development one), so your pull request is closed once its change is in, with
   credit in the changelog.

Add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for anything a user would notice.
