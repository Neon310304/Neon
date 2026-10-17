import pytest

from src.services.public_speech import PublicSpeechSegments, normalize_speech_text, speech_text


@pytest.mark.parametrize("text,expected", [
    ("BQL, bql! (BqL).", "ban quản lí, ban quản lí! (ban quản lí)."),
    ("ABQL BQLA BQL1 1BQL BQL_name đBQL BQLđ", "ABQL BQLA BQL1 1BQL BQL_name đBQL BQLđ"),
    ("BQL/BQL: cư dân", "ban quản lí/ban quản lí: cư dân"),
    ("ban quản lí", "ban quản lí"),
])
def test_normalize_standalone_abbreviation(text, expected):
    assert normalize_speech_text(text) == expected
    assert normalize_speech_text(expected) == expected


def test_speech_cleanup_normalizes_only_spoken_content():
    assert speech_text("**BQL** [bql](https://example.test) `BQL`\n```BQL``` 123456789012") == "ban quản lí ban quản lí ban quản lí"


def test_stream_normalizes_abbreviation_split_across_deltas():
    stream = PublicSpeechSegments()
    assert stream.append("Liên hệ B") == []
    assert stream.append("Q") == []
    segments = stream.append("L.")
    assert [segment["text"] for segment in segments] == ["Liên hệ ban quản lí."]
    assert stream.spoken == len("Liên hệ ban quản lí.")


def test_stream_budget_counts_expanded_text():
    stream = PublicSpeechSegments()
    segments = stream.append("BQL " * 1500, final=True)
    assert sum(len(segment["text"]) for segment in segments) == stream.spoken == 5000
    assert stream.truncated
