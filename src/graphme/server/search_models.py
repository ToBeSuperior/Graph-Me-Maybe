"""The search models run on this machine, as the settings page lists and changes them: each
with whether it is installed, installed or removed on request, the one searched with installed
on its own -- at start and when it is chosen -- and the space embedded with it once it is."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from graphme.adapters.local_models import LocalModels
from graphme.providers import LOCAL_EMBEDDERS, Embedder, ModelFile
from graphme.server.jobs import Busy

if TYPE_CHECKING:
    from graphme.server.state import Demo

_log = logging.getLogger(__name__)


@dataclass
class _Fetching:
    done: int = 0
    total: int = 0


class SearchModels:
    """The local search models of one server."""

    def __init__(self, demo: Demo) -> None:
        self.demo = demo
        self._models: LocalModels | None = None
        self._lock = threading.Lock()
        self._fetching: dict[str, _Fetching] = {}
        self._errors: dict[str, str] = {}

    @property
    def models(self) -> LocalModels:
        if self._models is None:
            from graphme.runtime import local_models  # the one per home, holding its servers

            settings = self.demo.settings
            self._models = local_models(settings.local_models or settings.data_dir)
        return self._models

    @models.setter
    def models(self, value: LocalModels) -> None:
        self._models = value

    @staticmethod
    def row(name: str) -> Embedder:
        row = LOCAL_EMBEDDERS.get(name)
        if row is None or row.model_file is None:
            raise LookupError(f"no search model called {name!r}")
        return row

    def listed(self) -> list[dict[str, Any]]:
        chosen = self.demo.settings.embedding_provider
        with self._lock:
            fetching = {n: {"done": f.done, "total": f.total} for n, f in self._fetching.items()}
            errors = dict(self._errors)
        rows = []
        for name, row in LOCAL_EMBEDDERS.items():
            assert row.model_file is not None
            rows.append({"name": name, "model": row.model, "size": row.model_file.size,
                         "installed": self.models.installed(row.model_file),
                         "chosen": name == chosen, "fetching": fetching.get(name),
                         "error": errors.get(name)})
        return rows

    def install(self, name: str, *, then_embed: bool = False) -> None:
        """`name`'s model fetched behind; once it is, the space embedded with it when it is the
        one searched with."""
        model = self.row(name).model_file
        assert model is not None
        with self._lock:
            if name in self._fetching:
                return
            self._fetching[name] = _Fetching()
            self._errors.pop(name, None)
        threading.Thread(target=self._fetch, args=(name, model, then_embed), daemon=True).start()

    def _fetch(self, name: str, model: ModelFile, then_embed: bool) -> None:
        def progress(done: int, total: int) -> None:
            with self._lock:
                self._fetching[name] = _Fetching(done, total)

        try:
            self.models.install(model, on_progress=progress)
        except Exception as error:  # noqa: BLE001 -- said on the page; the next start tries again
            _log.warning("the search model %s could not be installed: %s", name, error)
            with self._lock:
                self._errors[name] = str(error)
            return
        finally:
            with self._lock:
                self._fetching.pop(name, None)
        if then_embed or name == self.demo.settings.embedding_provider:
            self._embed()

    def remove(self, name: str) -> None:
        """`name`'s model deleted -- never the one searched with, nor one being fetched."""
        model = self.row(name).model_file
        assert model is not None
        if name == self.demo.settings.embedding_provider:
            raise Busy("the search model in use cannot be removed -- choose another first")
        with self._lock:
            if name in self._fetching:
                raise Busy("this search model is being installed")
        self.models.remove(model)

    def ensure_chosen(self) -> None:
        """The model searched with, installed if it is not -- and the space embedded with it."""
        name = self.demo.settings.embedding_provider
        row = LOCAL_EMBEDDERS.get(name)
        if row is None or row.model_file is None:
            return
        if self.models.installed(row.model_file):
            return
        self.install(name, then_embed=True)

    def chosen_changed(self) -> None:
        """Another model chosen: its passages' vectors made with it, once it is installed."""
        name = self.demo.settings.embedding_provider
        row = LOCAL_EMBEDDERS.get(name)
        if row is not None and row.model_file is not None and \
                not self.models.installed(row.model_file):
            self.install(name, then_embed=True)
            return
        self._embed()

    def embed_behind(self) -> None:
        """The space embedded with the chosen model, if it is installed: another graph opened
        may hold passages embedded with another, or none."""
        row = LOCAL_EMBEDDERS.get(self.demo.settings.embedding_provider)
        if row is None or row.model_file is None or self.models.installed(row.model_file):
            self._embed()

    def _embed(self) -> None:
        try:
            self.demo.start_embed()
        except Busy:
            pass  # a build is running: it embeds with the chosen model when it ends
        except Exception as error:  # noqa: BLE001 -- the space not open yet: embedded next build
            _log.warning("the space could not be embedded with the new search model: %s", error)
