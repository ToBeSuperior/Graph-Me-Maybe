"""A question is searched in the documents' language too. Invented data.

A question asked in English found Korean documents only through the vectors: the full-text
search matched none of its words. The question is split with a line of each language the
documents are written in -- shown, not named, since a script does not say which of its
languages a text is in -- and a query is written again in each one the question is not in.
"""
from __future__ import annotations

from graphme.adapters.storage.answer_prompt import (
    SPLIT_SYSTEM,
    asked_with_samples,
    language_samples,
)

KOREAN = "펌프 베어링은 2,000시간마다 점검한다. 온도가 85도를 넘으면 즉시 정지한다."
ENGLISH = "The pressure transmitter was calibrated against the reference gauge in March."
JAPANESE = "ポンプの軸受は2,000時間ごとに点検する。温度が85度を超えたら直ちに停止する。"


def test_one_line_of_each_script_the_documents_are_mostly_written_in() -> None:
    texts = [KOREAN, KOREAN.replace("펌프", "압축기"), ENGLISH, JAPANESE, "2025-03-01 12:00"]

    samples = language_samples(texts)

    # Most documents first; a line with no letters says nothing about a language.
    assert samples == [KOREAN, ENGLISH, JAPANESE]


def test_a_sample_is_one_short_line() -> None:
    long = " ".join([KOREAN] * 10)

    [sample] = language_samples([long])

    assert len(sample) <= 160 and long.startswith(sample.rstrip("…").rstrip())


def test_the_question_goes_with_the_documents_lines() -> None:
    asked = asked_with_samples("What is the bearing inspection interval?", [KOREAN, ENGLISH])

    assert asked.startswith("What is the bearing inspection interval?")
    assert KOREAN in asked and ENGLISH in asked
    assert "language" in SPLIT_SYSTEM and "samples" in SPLIT_SYSTEM


def test_with_no_documents_the_question_goes_alone() -> None:
    assert asked_with_samples("What is the interval?", []) == "What is the interval?"
