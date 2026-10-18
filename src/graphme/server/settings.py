"""The keys and model the server runs with, as the settings page shows and changes them:
a secret only as set or not, a change tried before it is written to `.env`."""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request

from graphme import model_endpoint
from graphme.adapters.chat import auth_headers
from graphme.config import _ENV_FIELDS, Settings, _read_dotenv, _read_yaml
from graphme.server.state import BUILDING, Busy, Demo

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _models(base_url: str, headers: dict[str, str]) -> str | None:
    """Why `base_url` would not serve these credentials -- "refused" or "unreachable" -- or
    None. Asks only for the model list: nothing is called, nothing is billed."""
    request = urllib.request.Request(f"{base_url}/models", headers=headers)
    try:
        urllib.request.urlopen(request, timeout=10).close()
    except urllib.error.HTTPError as error:
        return "refused" if error.code in (401, 403) else None
    except OSError:
        return "unreachable"
    return None


_CHOICES = (
    ("GRAPHME_PROVIDER", False),
    ("GRAPHME_EMBEDDING_PROVIDER", False),
    ("GRAPHME_MODEL_BASE_URL", False),
    ("GRAPHME_MODEL", False),
    ("GRAPHME_HIERARCHY_READINGS", False),
)
_ANY_KEY = {row.key_env for row in model_endpoint.PROVIDERS.values()} | {
    row.key_env for row in model_endpoint.EMBEDDERS.values()}


def editable(settings: Settings) -> tuple[tuple[str, bool], ...]:
    """What the settings page can change: the chosen provider's key, the embedder's key when
    it is another variable, and what the model is asked. Neo4j is not -- the server starts its
    own, and Aura is asked for only when exporting."""
    keys = [model_endpoint.PROVIDERS[settings.provider].key_env]
    if (row := model_endpoint.EMBEDDERS.get(settings.embedding_provider)) and \
            row.key_env not in keys:
        keys.append(row.key_env)
    return (*((key, True) for key in keys), *_CHOICES)


def _field(settings: Settings, env: str) -> str:
    """The settings field a variable fills: a key by the row that names it, the rest by
    `_ENV_FIELDS`."""
    row = model_endpoint.EMBEDDERS.get(settings.embedding_provider)
    if row is not None and env == row.key_env:
        return "embedding_key"
    if env == model_endpoint.PROVIDERS[settings.provider].key_env:
        return "api_key"
    return _ENV_FIELDS[env]


def described(settings: Settings) -> dict[str, Any]:
    fields = []
    for env, secret in editable(settings):
        value = getattr(settings, _field(settings, env))
        text = None if value is None else str(value)
        fields.append({"env": env, "secret": secret, "set": bool(text),
                       "value": None if secret else text,
                       "hint": text[-4:] if secret and text and len(text) > 8 else None})
    return {"endpoint": settings.provider, "fields": fields, "problems": preflight(settings)}


def from_here(request: Request) -> bool:
    """A request this machine's page sent: to a loopback host (not a name rebound to it), and,
    when the browser says where from, from a loopback page."""
    host = urlsplit(f"//{request.headers.get('host', '')}").hostname or ""
    origin = request.headers.get("origin")
    return host in LOOPBACK and (origin is None or urlsplit(origin).hostname in LOOPBACK)


def _pinned(env_file: Path, names: set[str]) -> list[str]:
    """Which of `names` are set where this page cannot clear them: the shell, or the
    GRAPHME_CONFIG file."""
    pinned = sorted(name for name in names if os.environ.get(name))
    config = {**_read_dotenv(env_file), **os.environ}.get("GRAPHME_CONFIG")
    try:
        written = _read_yaml(Path(config).expanduser()) if config else {}
    except (OSError, ValueError):
        written = {}  # an unreadable file is refused when the settings are read
    return pinned + sorted(f"{name} (GRAPHME_CONFIG)" for name in names
                           if name not in pinned and written.get(_ENV_FIELDS[name]))


def changed(
    demo: Demo,
    values: dict[str, str],
    env_file: Path,
    load: Callable[[dict[str, str]], Settings],
) -> Settings:
    """`values` tried, written to `env_file` and taken up by `demo` at once; the settings it
    runs with now. `load` reads settings from a set of variables, as the server started with
    them. Refused with `ValueError` -- nothing written -- for a name the page cannot change, a
    value that does not hold, or a key its server turns away; `Busy` while a build runs."""
    if demo.build.state in BUILDING:
        raise Busy("그래프를 만드는 중에는 설정을 바꿀 수 없습니다")
    values = {k: v.strip() for k, v in values.items() if v.strip()}
    unknown = sorted(set(values) - _ANY_KEY - {env for env, _ in _CHOICES})
    if unknown or any("\n" in v or "\r" in v for v in values.values()):
        raise ValueError(f"바꿀 수 없는 설정입니다: {', '.join(unknown) or '줄바꿈'}")
    if over := sorted(set(values) & set(os.environ)):
        raise ValueError(f"환경변수 {', '.join(over)}가 .env보다 우선합니다 -- "
                         "셸에서 지우고 서버를 다시 시작하세요")
    dropped: set[str] = set()
    if values.get("GRAPHME_PROVIDER", demo.settings.provider) != demo.settings.provider:
        # The old provider's address and model would take the new provider's key there.
        dropped = {"GRAPHME_MODEL_BASE_URL", "GRAPHME_MODEL"} - set(values)
        if pinned := _pinned(env_file, dropped):
            raise ValueError(f"{', '.join(pinned)}가 이전 제공자의 값으로 고정돼 "
                             "있습니다 -- 지우고 서버를 다시 시작하세요")
    stored = {k: v for k, v in _read_dotenv(env_file).items() if k not in dropped}
    try:
        new = load({**stored, **os.environ, **values})
    except ValueError as error:
        raise ValueError(f"설정 값이 맞지 않습니다: {error}") from error
    chat_key = model_endpoint.PROVIDERS[new.provider].key_env
    embed_row = model_endpoint.EMBEDDERS.get(new.embedding_provider)
    chat_headers = auth_headers(model_endpoint.PROVIDERS[new.provider].wire, new.api_key)
    checks: list[tuple[str, Callable[[], str | None]]] = [
        (chat_key, lambda: _models(new.base_url, chat_headers)),
        ("GRAPHME_MODEL_BASE_URL", lambda: _models(new.base_url, chat_headers)),
    ]
    if embed_row is not None and new.embedding_key:  # a test embedder has no key to try
        embed_headers = auth_headers("chat_completions", new.embedding_key)
        checks.insert(0, (embed_row.key_env, lambda: _models(embed_row.base_url, embed_headers)))
    for env, tried in checks:
        if env in values and tried() == "refused":
            raise ValueError(f"{env}: 서버가 키를 거절했습니다 -- 저장하지 않았습니다")
    write_env(env_file, values, drop=dropped)
    demo.reconfigure(new)
    return new


def write_env(path: Path, values: dict[str, str], drop: set[str] | None = None) -> None:
    """`values` into the `.env` at `path`, each in place of its line or added at the end; the
    lines of `drop` removed, every other line kept as it was. Readable by its owner only -- it
    holds keys."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    if drop:
        lines = [line for line in lines if line.lstrip().startswith("#")
                 or line.strip().removeprefix("export ").partition("=")[0].strip() not in drop]
    left = dict(values)
    for at, line in enumerate(lines):
        name = line.strip().removeprefix("export ").partition("=")[0].strip()
        if name in left and not line.lstrip().startswith("#"):
            lines[at] = f"{name}={left.pop(name)}"
    lines += [f"{name}={value}" for name, value in left.items()]
    temporary = path.with_name(f".{path.name}.writing")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def preflight(settings: Settings) -> list[str]:
    """What keeps the server from building or answering, said before it starts rather than on
    the first question: the graph embedding key, the chat model's address and key, the viewer."""
    problems = []
    chat_key = model_endpoint.PROVIDERS[settings.provider].key_env
    embed_row = model_endpoint.EMBEDDERS.get(settings.embedding_provider)
    if embed_row is None:  # a test embedder: no key to ask for
        pass
    elif not settings.embedding_key:
        problems.append(f"{embed_row.key_env}가 비어 있습니다 -- 그래프 임베딩과 검색에 쓰는"
                        " 키를 .env에 적으세요.")
    elif _models(embed_row.base_url,
                 auth_headers("chat_completions", settings.embedding_key)) == "refused":
        problems.append(f"{settings.embedding_provider}가 {embed_row.key_env}를 거절했습니다 -- "
                        "제공자의 API 키인지 확인하세요.")
    chat = _models(settings.base_url,
                   auth_headers(model_endpoint.PROVIDERS[settings.provider].wire, settings.api_key))
    if chat == "refused" and not settings.api_key:
        problems.append(f"{chat_key}가 비어 있습니다 -- 문서 읽기와 답변에 쓰는 키를"
                        " 넣으세요.")
    elif chat == "refused":
        problems.append(f"모델 서버({settings.base_url})가 키를 거절했습니다 -- .env의 키를"
                        " 확인하세요.")
    elif chat == "unreachable":
        problems.append(f"모델 서버({settings.base_url})에 연결할 수 없습니다 -- 켜져 있는지"
                        " 확인하세요.")
    if not (settings.viewer_directory / "dist").is_dir():
        problems.append(f"뷰어 번들이 {settings.viewer_directory}에 없습니다 -- DOCX·PPTX·XLSX를"
                        " 그리려면 src/graphme/adapters/rendering/viewer/setup.sh를 실행하세요.")
    return problems
