from __future__ import annotations

import contextlib
import logging
import ntpath
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from graphme import model_endpoint
from graphme.providers import DEFAULT_EMBEDDER

_log = logging.getLogger(__name__)

RELEVANCE_FLOORS = {"text-embedding-3-large": 0.2, "harrier-oss-v1-0.6b": 0.39, "KURE-v1": 0.46}
"""The relevance floor measured for each embedding model, on the invented sample documents
(src/graphme/samples.py). GRAPHME_RELEVANCE_FLOOR wins over it. The two run on this machine
were set midway between the lowest on-topic and the highest off-topic question's nearest
cosine, six of each in English and six in Korean: harrier 0.41 / 0.37, KURE 0.48 / 0.44."""

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
"""This machine's names. `[::1]` is how a Host header writes the IPv6 one."""

_SECRET_CONFIG_KEYS = {"api_key", "embedding_key", "neo4j_password", "server_key",
                       "box_client_secret"}
ENV_FIELDS = {
    "GRAPHME_PROFILE": "profile",
    "GRAPHME_DATA_DIR": "data_dir",
    "GRAPHME_PROVIDER": "provider",
    "GRAPHME_MODEL_BASE_URL": "base_url",
    "GRAPHME_MODEL": "model",
    "GRAPHME_EMBEDDING_PROVIDER": "embedding_provider",
    "GRAPHME_EMBEDDING_MODEL": "embedding_model",
    "GRAPHME_EMBEDDING_DIMENSIONS": "embedding_dimensions",
    "GRAPHME_RETRIEVAL_TOP_K": "retrieval_top_k",
    "GRAPHME_RELEVANCE_FLOOR": "relevance_floor",
    "GRAPHME_HYBRID_DENSE_WEIGHT": "hybrid_dense_weight",
    "GRAPHME_RRF_K": "rrf_k",
    "GRAPHME_VIEWER_DIR": "viewer_directory",
    "GRAPHME_RENDER_TIMEOUT_SECONDS": "render_timeout_seconds",
    "GRAPHME_CHROME_EXECUTABLE": "chrome_executable",
    "GRAPHME_HIERARCHY_READINGS": "hierarchy_readings",
    "GRAPHME_NEO4J_URI": "neo4j_uri",
    "GRAPHME_NEO4J_USER": "neo4j_user",
    "GRAPHME_NEO4J_PASSWORD": "neo4j_password",
    "GRAPHME_NEO4J_HEAP": "neo4j_heap",
    "GRAPHME_API_KEY": "server_key",
    "GRAPHME_NEO4J_PAGECACHE": "neo4j_pagecache",
    "GRAPHME_BOX_CLIENT_ID": "box_client_id",
    "GRAPHME_BOX_CLIENT_SECRET": "box_client_secret",
}
_FLOAT_FIELDS = {"render_timeout_seconds", "relevance_floor", "hybrid_dense_weight"}
_INT_FIELDS = {
    "rrf_k",
    "hierarchy_readings",
    "embedding_dimensions",
    "retrieval_top_k",
}
_TEST_EMBEDDERS = {"fake", "hash"}


def default_embedder(provider: str) -> str:
    """The general model run on this machine, whoever answers the questions: searching then
    needs no key and sends nothing off it."""
    return DEFAULT_EMBEDDER


CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)


def chrome_paths(environ: Mapping[str, str] = os.environ) -> tuple[str, ...]:
    """Where Chrome is installed by default: macOS (machine or user), Linux, and Windows
    (machine or user)."""
    mac_user = (f"{environ['HOME']}{CHROME_PATHS[0]}",) if environ.get("HOME") else ()
    windows = tuple(
        ntpath.join(environ[root], "Google", "Chrome", "Application", "chrome.exe")
        for root in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if environ.get(root)
    )
    return (*CHROME_PATHS, *mac_user, *windows)


def _installed_chrome() -> str | None:
    return next((path for path in chrome_paths() if Path(path).is_file()), None)


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not name:
            raise ValueError(f"invalid .env entry at {path}:{line_number}")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: Literal["local", "test"] = "local"
    data_dir: Path = Path(".graphme")
    provider: str = model_endpoint.DEFAULT_PROVIDER
    """Who answers every chat call: a name in `model_endpoint.PROVIDERS`."""
    base_url: str = ""
    """GRAPHME_MODEL_BASE_URL; the provider's own address when unset. Named `base_url`, not
    `model_base_url`: Pydantic reserves the `model_` prefix."""
    model: str = ""
    """GRAPHME_MODEL; the provider's default model when unset."""
    api_key: str | None = Field(default=None, repr=False)
    """Read only from the chosen provider's own variable."""
    embedding_provider: str = ""
    """The one embedder of the product (`model_endpoint.EMBEDDERS`); `hash` and `fake` only
    with GRAPHME_PROFILE=test. Unset: `default_embedder(provider)`."""
    embedding_model: str = ""
    embedding_dimensions: int = Field(default=3072, gt=0)
    embedding_key: str | None = Field(default=None, repr=False)
    """Read only from the embedder's own variable."""
    retrieval_top_k: int = Field(default=8, gt=0)
    relevance_floor: float = Field(default=0.3, ge=0, le=1)
    """How close (cosine) a passage must come to the question to be kept by its meaning -- the
    one place this is set. A passage below it is kept only if it holds the question's words
    (`graph_rag.every_word`); none kept, and nothing is shown or answered from. The hybrid score
    cannot tell this, since it ranks each hit against the best one of the same search. Set for
    `text-embedding-3-large` in `RELEVANCE_FLOORS`, measured on the invented sample documents
    (src/graphme/samples.py): questions about them come to 0.41 and above, questions about
    nothing in them to 0.16 and below. A question about a document as a whole -- its cover, its
    date, how many charts it has -- comes near no passage either (0.30 and below), and what slips
    past the floor is refused by the answer, which says a
    question cannot be answered when the context does not establish what it asks about. This
    default is for an uncalibrated model; another model spreads cosines differently, and its
    own value goes in `RELEVANCE_FLOORS`."""
    hybrid_dense_weight: float = Field(default=0.6, ge=0, le=1)
    """How much the vector search counts against the fulltext search when the two rankings are
    fused (reciprocal rank fusion); the fulltext search counts the rest. Leaning a little to
    meaning follows English-language results that cosine serves general questions better and
    that unweighted RRF is hard to beat without tuning (Cormack et al., SIGIR 2009; Bruch et al.,
    TOIS 2023). A question naming a document or part number gets the two the other way round,
    as BM25 serves specific questions better (arXiv:2504.05324). A starting point, to be tuned
    on questions about one's own documents."""
    rrf_k: int = Field(default=60, gt=0)
    """Reciprocal rank fusion's constant: a passage ranked r counts weight / (rrf_k + r). 60 is
    the value Cormack et al. fixed and the common default since."""
    viewer_directory: Path = Field(
        default_factory=lambda: Path.home() / ".cache/graphme/ooxml-viewer"
    )
    """OOXML viewer bundle; build it with adapters/rendering/viewer/setup.sh."""
    render_timeout_seconds: float = Field(default=300.0, gt=0)
    chrome_executable: str | None = Field(default_factory=_installed_chrome)
    """The browser that draws OOXML pages: the Chrome installed on this machine, unless set.
    With none, Playwright's own browser (`playwright install chromium --only-shell`)."""
    hierarchy_readings: int = Field(default=1, ge=1, le=4)
    """How many independent readings each page gets. Above one, they run together and
    where they disagree the model is shown both beside the page and picks one -- a page
    costs that many calls, plus one more only when the readings actually differ."""
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    """The graph space a corpus is put in (`graphme build-graph`), when it is not the app's own
    (`local_neo4j`): a checkout's, someone's own Neo4j, Aura."""
    local_models: Path | None = None
    """The app's home, where a search model run on this machine is kept (`models/`) with the
    server that runs it (`llama/`); the data folder when no home is named."""
    local_neo4j: Path | None = None
    """The app's home, whose own Neo4j is the graph space (`local_neo4j.py`): set whenever the
    settings name no GRAPHME_NEO4J_URI."""
    local_graph: Path | None = None
    """The folder of the workspace whose graph the app's own Neo4j runs over (`neo4j-data/`
    in it); None for the home's own graph. Set by the server, never read from the environment."""
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = Field(default=None, repr=False)
    """Only for a Neo4j run with a password; the one started here needs none."""
    server_key: str | None = Field(default=None, repr=False)
    box_client_id: str | None = None
    """The user's own Box app (OAuth 2.0, user authentication) that reads what they pick."""
    box_client_secret: str | None = Field(default=None, repr=False)
    """GRAPHME_API_KEY: what `graphme serve` asks every `/v1` request for. Unset, it asks for
    nothing, and serves this machine only."""
    neo4j_heap: str = Field(default="1g", pattern=r"^[1-9][0-9]*[kmg]$")
    neo4j_pagecache: str = Field(default="512m", pattern=r"^[1-9][0-9]*[kmg]$")
    """The heap and page cache of the app's own Neo4j. The page cache holds the passages'
    vectors: a space of thousands of documents wants it at least their size. Changed, it is
    taken up on the next start."""

    @field_validator("data_dir", "viewer_directory")
    @classmethod
    def expand_home(cls, value: Path) -> Path:
        """`~` as a shell would read it -- `.env` is not read by one."""
        return value.expanduser()

    @model_validator(mode="before")
    @classmethod
    def provider_defaults(cls, data: Any) -> Any:
        """What was not given comes from the chosen provider's and embedder's rows."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        provider = data.setdefault("provider", model_endpoint.DEFAULT_PROVIDER)
        chat = model_endpoint.PROVIDERS.get(provider)
        if chat is None:
            known = ", ".join(model_endpoint.PROVIDERS)
            raise ValueError(f"unknown GRAPHME_PROVIDER {provider!r}; this build knows: {known}")
        data.setdefault("base_url", chat.base_url)
        data.setdefault("model", chat.model)
        embedder = model_endpoint.EMBEDDERS.get(
            data.setdefault("embedding_provider", default_embedder(provider)))
        if embedder is not None:
            data.setdefault("embedding_model", embedder.model)
            data.setdefault("embedding_dimensions", embedder.dimensions)
        if "relevance_floor" not in data:
            model = data.get("embedding_model")
            if model in RELEVANCE_FLOORS:
                data["relevance_floor"] = RELEVANCE_FLOORS[model]
            elif embedder is not None:
                _log.warning("the relevance floor is not calibrated for %s -- "
                             "set GRAPHME_RELEVANCE_FLOOR", model)
        return data

    @field_validator("base_url")
    @classmethod
    def require_an_allowed_endpoint(cls, value: str) -> str:
        return model_endpoint.check_base_url(value)

    @model_validator(mode="after")
    def restrict_test_providers(self) -> Settings:
        if self.embedding_provider in _TEST_EMBEDDERS:
            if self.profile != "test":
                raise ValueError("fake embedding providers require GRAPHME_PROFILE=test")
        elif self.embedding_provider not in model_endpoint.EMBEDDERS:
            known = ", ".join(model_endpoint.EMBEDDERS)
            raise ValueError(
                f"unknown GRAPHME_EMBEDDING_PROVIDER {self.embedding_provider!r}; "
                f"this build knows: {known}"
            )
        return self

    @property
    def embeds(self) -> bool:
        """Whether passages can be embedded: with the embedder's key, or by a model run on this
        machine, which needs none."""
        row = model_endpoint.EMBEDDERS.get(self.embedding_provider)
        return self.embedding_key is not None or (row is not None and row.model_file is not None)

    @property
    def asset_dir(self) -> Path:
        return self.data_dir / "assets"


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"configuration file does not exist: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("configuration file must contain a YAML mapping")
    forbidden = _SECRET_CONFIG_KEYS.intersection(raw)
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise ValueError(f"secrets must be supplied by environment variables, not YAML: {names}")
    return raw


def app_home(environ: Mapping[str, str] = os.environ, platform: str = sys.platform) -> Path:
    """Where the app keeps its settings, its graph data and the installed assets."""
    if named := environ.get("GRAPHME_HOME"):
        return Path(named)
    if platform == "win32":
        return Path(environ["LOCALAPPDATA"]) / "Graph-Me-Maybe"
    if platform == "darwin":
        return Path(environ["HOME"]) / "Library" / "Application Support" / "Graph-Me-Maybe"
    data = environ.get("XDG_DATA_HOME") or str(Path(environ["HOME"]) / ".local" / "share")
    return Path(data) / "graph-me-maybe"


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    base: Path | None = None
    if environ is None:
        # A checkout's `.env` is in the working folder; anywhere else a command uses the
        # installed app's settings and data, as `graphme app` does.
        base = Path.cwd()
        if not (base / ".env").is_file():
            try:
                app = app_home(os.environ)
            except KeyError:  # no home directory named in this environment
                app = base
            if (app / ".env").is_file():
                base = app
        env = read_dotenv(base / ".env")
        env.update(os.environ)
    else:
        env = dict(environ)
    values: dict[str, Any] = {}
    if config_path := env.get("GRAPHME_CONFIG"):
        values.update(read_yaml(Path(config_path).expanduser()))

    for env_name, field_name in ENV_FIELDS.items():
        if env_name not in env or env[env_name] == "":
            continue
        value: Any = env[env_name]
        if field_name in _INT_FIELDS:
            try:
                value = int(value)
            except ValueError as error:
                raise ValueError(f"{env_name} must be an integer") from error
        elif field_name in _FLOAT_FIELDS:
            try:
                value = float(value)
            except ValueError as error:
                raise ValueError(f"{env_name} must be a number") from error
        values[field_name] = value

    # Each key is read only from the variable its provider's row names, and nowhere else:
    # another provider's key in the environment is never picked up.
    provider = values.get("provider") or model_endpoint.DEFAULT_PROVIDER
    embedder = values.get("embedding_provider") or default_embedder(provider)
    if (chat := model_endpoint.PROVIDERS.get(provider)) and env.get(chat.key_env):
        values["api_key"] = env[chat.key_env]
    if (row := model_endpoint.EMBEDDERS.get(embedder)) and env.get(row.key_env):
        values["embedding_key"] = env[row.key_env]
    if base is not None and base != Path.cwd() and "data_dir" not in values:
        values["data_dir"] = base / ".graphme"
    with contextlib.suppress(KeyError):  # no home directory named in this environment
        values.setdefault("local_models", app_home(env))
    if base is not None and "neo4j_uri" not in values:
        # No Neo4j named: the app's own, in its home, is the graph space -- for the app, a
        # command anywhere, and a checkout alike.
        with contextlib.suppress(KeyError):  # no home directory named in this environment
            values["local_neo4j"] = app_home(env)
    if "viewer_directory" not in values:
        # The viewer the installer put in the app's home: every command draws with it, not
        # only `graphme app`.
        try:
            installed = app_home(env) / "viewer"
        except KeyError:  # no home directory named in this environment
            installed = None
        if installed is not None and (installed / "dist").is_dir():
            values["viewer_directory"] = installed
    return Settings.model_validate(values)
