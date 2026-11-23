"""A budget in tokens is spent as the text costs: a CJK character is about a token, as a
Hangul syllable is, not three bytes over four."""
from graphme.domain.text_match import estimated_tokens


def test_chinese_and_japanese_count_a_token_a_character_like_korean() -> None:
    assert estimated_tokens("保証期間は二年です") == 9  # invented: "the warranty is two years"
    assert estimated_tokens("保修期为两年") == 6
    assert estimated_tokens("보증기간은이년") == 7
    assert estimated_tokens("warranty") == 2
