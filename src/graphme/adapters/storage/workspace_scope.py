"""A workspace held apart inside the one graph DB.

Neo4j Community serves one database, so the workspaces share it: a workspace's units carry
`W_<id>`, its passages `C_<id>` (apart, so the fulltext index on passage text never holds a
unit's text), each with its own uniqueness on `id` -- ids come from a file's content, so the
same file in two workspaces has the same ids -- and its own vector and fulltext indexes. Every
query the storage adapters send goes through `WorkspaceScope.cypher`, which names the
workspace's label in every node pattern; `unscoped_patterns` finds one that does not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SCOPE_LABEL = re.compile(r"^[WC]_[A-Za-z0-9_]+$")
_CHUNK = "Chunk"


@dataclass(frozen=True)
class WorkspaceScope:
    workspace_id: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_]+", self.workspace_id):
            raise ValueError(f"not a workspace id for a label: {self.workspace_id!r}")

    @property
    def node_label(self) -> str:
        return f"W_{self.workspace_id}"

    @property
    def chunk_label(self) -> str:
        return f"C_{self.workspace_id}"

    @property
    def vector_index(self) -> str:
        return f"chunk_embedding_{self.workspace_id}"

    @property
    def text_index(self) -> str:
        return f"chunk_text_{self.workspace_id}"

    def cypher(self, query: str) -> str:
        """The query with this workspace's label added to every node pattern that names a
        label: `C_<id>` beside `Chunk`, `W_<id>` beside anything else. A pattern already
        naming a workspace label is left as it is."""
        out: list[str] = []
        last = 0
        for _start, end, labels in _node_label_chains(query):
            if any(_SCOPE_LABEL.match(label) for label in labels):
                continue
            mine = self.chunk_label if _CHUNK in labels else self.node_label
            out += [query[last:end], f":{mine}"]
            last = end
        out.append(query[last:])
        return "".join(out)


DEFAULT_SCOPE = WorkspaceScope("default")
"""The home's own graph."""


def unscoped_patterns(query: str) -> list[str]:
    """The node patterns in `query` naming labels but no workspace label, as written up to
    their last label ("(n:DocNode")."""
    found = []
    for start, end, labels in _node_label_chains(query):
        if not any(_SCOPE_LABEL.match(label) for label in labels):
            opening = query.rfind("(", 0, start)
            found.append(query[opening:end])
    return found


def _node_label_chains(query: str) -> list[tuple[int, int, list[str]]]:
    """Each run of labels (`:A:B`) written directly inside a node pattern's parentheses: its
    start, its end and its labels. Labels in a relationship (`[r:TYPE]`), a map
    (`{id: x}`), a string, or a `SET n:Label` outside any pattern are not node patterns."""
    chains: list[tuple[int, int, list[str]]] = []
    stack: list[str] = []
    i, quote = 0, ""
    while i < len(query):
        character = query[i]
        if quote:
            if character == "\\":
                i += 2
                continue
            if character == quote:
                quote = ""
        elif character in "'\"`":
            quote = character
        elif character in "([{":
            stack.append(character)
        elif character in ")]}":
            if stack:
                stack.pop()
        elif character == ":" and stack and stack[-1] == "(" and _after_variable(query, i):
            start, labels = i, []
            while i < len(query) and query[i] == ":":
                name = _LABEL.match(query, i + 1)
                if name is None:
                    break
                labels.append(name.group())
                i = name.end()
            if labels:
                chains.append((start, i, labels))
            continue
        i += 1
    return chains


def _after_variable(query: str, colon: int) -> bool:
    """Whether the colon follows a node's variable or opens the pattern: `(n:`, `(:` --
    not a map key, a parameter or a property access."""
    before = query[:colon].rstrip()
    opening = before.rfind("(")
    between = before[opening + 1:]
    return bool(re.fullmatch(r"\s*[A-Za-z_][A-Za-z0-9_]*\s*|\s*", between))
