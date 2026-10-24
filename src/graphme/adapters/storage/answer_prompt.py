"""What the model answering a question over the graph is told: the system prompt, the
question under its context in its own language, and how a question asking several things is
cut into one search each."""
from __future__ import annotations

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
    "queries. Return only JSON matching the schema."
)
"""How a question asking several things -- a problem and how competitors handled it -- is
cut into one search each: one search for both finds what the nearer part is about."""
MAX_QUERIES = 3


class _Split(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=MAX_QUERIES)


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
