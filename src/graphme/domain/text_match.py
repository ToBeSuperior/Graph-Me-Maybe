"""How two pieces of text are compared when the question is whether they say the same name.

A page and a model's answer spell a name differently in ways that say nothing about which
name it is: case, how lines broke, and -- for a title -- a hyphen lost at a line break or a
punctuation mark added. Every comparison of that kind goes through here.
"""
from __future__ import annotations

import re
import unicodedata

_NOT_LETTERS = re.compile(r"[\W_]+")


def normalized(text: str) -> str:
    """One spelling for one character: Hangul composed as macOS file names do not keep it,
    full-width letters and colons as their plain forms, a ligature as its letters (NFKC)."""
    return unicodedata.normalize("NFKC", text)


def encodable(text: str) -> str:
    """`text` as something UTF-8 can write: a lone UTF-16 surrogate -- half a pair, which
    pymupdf returns for a glyph some fonts map that way -- as U+FFFD, and two halves of one
    pair as the character they make. A lone surrogate failed a whole build where the text
    was finally encoded, so a PDF's text is passed through here where it is read."""
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def folded(text: str) -> str:
    """Case and how lines broke do not matter: one space, lower case."""
    return " ".join(normalized(text).split()).casefold()


def letters(text: str) -> str:
    """Only letters and digits, lower case -- for a title, where punctuation is noise."""
    return _NOT_LETTERS.sub("", normalized(text).casefold())


def without(text: str, name: str) -> str:
    """The text with every occurrence of `name` taken out, whatever its case."""
    if not name:
        return folded(text)
    return folded(re.sub(re.escape(normalized(name)), " ", normalized(text), flags=re.IGNORECASE))


def estimated_tokens(text: str) -> int:
    """About how many tokens a text costs.

    Korean is three bytes a character in UTF-8 and about a token a character, so counting
    bytes over four -- which is close enough for English -- puts Korean text at a third of
    its real size and any budget would be spent long before it is reached.
    """
    hangul = sum(1 for character in text if "가" <= character <= "힣")
    return hangul + max(0, len(text.encode("utf-8")) - hangul * 3) // 4
