"""What the model answering a question over the graph is told: the system prompt, the
question under its context in its own language, and how a question asking several things is
cut into one search each."""
from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from pydantic import BaseModel, Field

SYSTEM = (
    "Answer the question using only the provided context. Answer in the language the "
    "question is written in, whatever language the context is in; when the question shows no "
    "language of its own -- only numbers, codes or names -- answer in English. Write the "
    "whole answer in "
    "that one language: when it is Korean, write Korean "
    "(Hangul) throughout, and never use Chinese characters (Hanzi) or Japanese, not even for "
    "a single word; keep a term in its original form only where the context writes it so, "
    "such as a part number, a unit or a standard's name. End each statement with the "
    "[file name] of the context it rests on. If the "
    "context does not hold the answer, say that it cannot be found in the context, and "
    "cite nothing. If the context does not establish what the question presupposes -- the "
    "entity, period, group or document it names -- say that the question cannot be answered "
    "from the documents rather than answering about something similar: a figure for another "
    "group, year or document is not an answer about the one asked for. A document applies "
    "to what it names -- a product, a model, a project, a "
    "period: where the context's documents are about different things, say which applies "
    "to what the question asks, and do not carry a rule from one to another."
)
SPLIT_SYSTEM = (
    "Split the user's question into the separate things it asks for, each written as a "
    "search query of its own, in the question's language. Keep every name, model, number, "
    "year and term the question uses in each query that is about it -- a query is searched "
    "alone and must say what it is about. A question that asks for one thing comes back as "
    "that one query, as written: never several wordings, synonyms or guesses at causes of "
    "the same thing -- each query must ask for something the others do not. At most three "
    "queries. In `documents`, copy as written each phrase of the question that names one "
    "particular document -- by its number, its file name, its title or what it is called -- "
    "and nothing that only says what kind of document is wanted; an empty list when it names "
    "none. Return only JSON matching the schema.\n\n"
    "When samples of the documents searched follow the question, a line of each language "
    "they are written in, put in `translated` each query again in every sample's language "
    "that is not the question's, as those documents would word it -- names, numbers and "
    "codes kept as written. Leave `translated` empty when every sample is in the question's "
    "language or no samples follow."
)
"""How a question asking several things -- a problem and how competitors handled it -- is
cut into one search each: one search for both finds what the nearer part is about."""
MAX_QUERIES = 3


class _Split(BaseModel):
    """The split as the model wrote it. A list past what is asked for is cut where it is
    read (`GraphRag._split_question`), not thrown away whole with the rest of the answer."""

    queries: list[str] = Field(min_length=1)
    documents: list[str] = Field(default_factory=list)
    """Phrases of the question naming one particular document, as written."""
    translated: list[str] = Field(default_factory=list)
    """The queries again in the documents' languages the question is not in."""


TEMPLATE = """Context:
{context}

Question:
{query_text}

Answer:"""


def _prompt(question: str, context: str) -> str:
    """The question under its context, and for a Korean or an English question -- or one with
    no language of its own, which is answered in English -- the language said again beside
    it: told only in the system prompt, ahead of a long context, a smaller model answered
    Korean questions in English, and English questions in Korean."""
    prompt = TEMPLATE.format(query_text=question, context=context)
    if _is_korean(question):
        return prompt.replace("\n\nAnswer:", "\n\nAnswer in Korean:")
    if _is_english(question):
        return prompt.replace("\n\nAnswer:", "\n\nAnswer in English:")
    return prompt


def _is_korean(text: str) -> bool:
    return any("\uac00" <= character <= "\ud7a3" for character in text)


def _is_english(text: str) -> bool:
    """English, or no language of its own (a number, a code): English is the default."""
    return all(character.isascii() for character in text if character.isalpha())


def _nothing_found(question: str) -> str:
    """What is said when nothing in the graph is near the question, in its language."""
    if _is_korean(question):
        return "그래프에서 이 질문과 관련된 문서를 찾지 못했습니다."
    return "No document in the graph is related to this question."


SAMPLE_CHARS = 160
MAX_LANGUAGES = 3


def language_samples(texts: Sequence[str]) -> list[str]:
    """One short line for each script the documents are mostly written in, the script most
    documents use first. A script does not say which of its languages a text is in, so the
    language is shown rather than named; a line with no letters says nothing about one."""
    by_script: dict[str, list[str]] = {}
    for text in texts:
        script = _script(text)
        if script is not None:
            by_script.setdefault(script, []).append(text)
    ranked = sorted(by_script.values(), key=len, reverse=True)[:MAX_LANGUAGES]
    return [_line(found[0]) for found in ranked]


def asked_with_samples(question: str, samples: Sequence[str]) -> str:
    """The question, then a line of each language the documents are written in."""
    if not samples:
        return question
    lines = "\n".join(f"- {sample}" for sample in samples)
    return f"{question}\n\nThe documents searched are written like these samples:\n{lines}"


def _script(text: str) -> str | None:
    counts: dict[str, int] = {}
    for character in text:
        if character.isalpha():
            name = unicodedata.name(character, "").split(" ")[0]
            # Japanese writes Han characters among its kana; Chinese writes no kana.
            script = "KANA" if name in ("HIRAGANA", "KATAKANA") else name
            counts[script] = counts.get(script, 0) + 1
    if not counts:
        return None
    return "KANA" if "KANA" in counts else max(counts, key=lambda key: counts[key])


def _line(text: str) -> str:
    line = " ".join(text.split())
    if len(line) <= SAMPLE_CHARS:
        return line
    cut = line[: SAMPLE_CHARS - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"
