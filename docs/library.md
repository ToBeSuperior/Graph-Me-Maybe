# Graph-Me-Maybe as a Python library

```bash
pip install graph-me-maybe              # the CLI and the Python API
pip install "graph-me-maybe[langchain]" # + the LangChain loader
pip install "graph-me-maybe[server]"    # + graphme serve (the HTTP API and the web app)
pip install "graph-me-maybe[app]"       # + the app: its window, tray and MCP server
```

Python 3.11 or later. The import name is `graphme`.

## Reading only (no Neo4j)

```python
import graphme

doc = graphme.read("report.xlsx", provider="openai", api_key="...")
for p in graphme.passages(doc):
    print(p.text, p.metadata)   # file, format, headings, sheet/cells, slide, pages
```

`read` draws each page and has the vision model read its outline, so it needs a provider key.
`passages` cuts the document by the same rule the graph uses, so a passage here and one in the
app are the same text.

With LangChain:

```python
from graphme.langchain import GraphMeLoader

docs = GraphMeLoader("reports/", provider="openai", api_key="...").load()
```

One LangChain `Document` per passage. Its metadata is the passage's metadata plus `node_ids`.

## The whole pipeline

```python
import graphme

with graphme.GraphMe(provider="openai", api_key="...") as g:
    g.add("reports/")       # read, put in the graph, linked
    g.embed()               # passages cut and embedded
    print(g.ask("What was Q3 revenue?").text)
```

With no `neo4j_uri`, the library runs its own Neo4j in its home folder. `GraphMe(workspace="Audits")`
opens one of the app's workspaces, made when missing; the workspace open in the app stays as it
is.

## Public names

| Name | What it does |
|---|---|
| `read(path, **settings)` | One file read whole: its outline, units and visuals. No graph. |
| `passages(document)` | The document cut into `Passage`s (`text`, `metadata`, `node_ids`, `to_dict()`). |
| `GraphMe(settings=None, *, workspace=None, **settings)` | The graph space. |
| `GraphMe.add(path)` | A folder or a file read, put in the space and linked; a `BuildReport`. |
| `GraphMe.embed()` | Passages cut and embedded for documents not embedded yet. |
| `GraphMe.search(question, documents=None)` | The passages a question finds (`GraphSource`s), with linked documents. |
| `GraphMe.ask(question, documents=None)` | An answer citing its files (`GraphAnswer`). |
| `GraphMe.documents()` | Every document in the space: id → file name. |
| `GraphMe.delete(ids)`, `GraphMe.reset()` | Documents taken out; every document taken out. |
| `GraphMe.export(target, dump=False)` | The space as Neo4j loads it: an import folder, or one `.dump`. |
| `setup(server=False, **settings)` | Fetch now what is otherwise fetched on first use. |
| `Settings`, `load_settings(**settings)` | Settings, from the environment and `.env` with keywords over them. |
| `graphme.langchain.GraphMeLoader(paths, **settings)` | The LangChain loader (`[langchain]`). |

Settings taken as keywords: `provider`, `api_key`, `model`, `base_url`, `embedding_provider`,
`embedding_key`, `embedding_model`, `neo4j_uri`, `neo4j_user`, `neo4j_password`, `home`. Anything
not passed is read from the environment and `.env`. Keys never appear in a repr, a log line or an
error message.

The API follows the app's version (`0.1.x`, so it may still change). A name that is removed or
renamed first warns (`DeprecationWarning`) for at least one release.

## What is fetched, and where

Each item is fetched the first time it is needed and checked against its published checksum.
`graphme setup` (or `graphme.setup()`) fetches them ahead of time, for CI or an offline machine.

| What | When | Where from |
|---|---|---|
| The viewer that draws DOCX, PPTX, XLSX and HWP | The first such page drawn (PDF needs none) | This version's GitHub release |
| A Chromium | Then too, unless Google Chrome is installed | Playwright |
| Neo4j and a Java runtime | The first `GraphMe` with no `neo4j_uri` | Neo4j, Eclipse Temurin |
| The search model and `llama-server` | The first `embed`, `search` or `ask` with `embedding_provider="general"` or `"korean"` | Hugging Face, llama.cpp |
| The web app | The first `graphme serve` outside a checkout | This version's GitHub release |

## What leaves the machine

The pages and text of the documents you read go to the provider you chose, for the outline, the
captions and the answers. With a provider embedder (`openai`, `gemini`), the passages go to it
too. Nothing else is sent: the graph, the files and the search model (`general`, `korean`) stay
on this machine.

## Examples

- [`examples/reading_only.ipynb`](../examples/reading_only.ipynb): read the invented samples into passages.
- [`examples/whole_pipeline.ipynb`](../examples/whole_pipeline.ipynb): build them into a graph and ask.
