"""Unit tests for the resilient JSON parsing ported from n8n ``Parse2``."""

from __future__ import annotations

import pytest

from app.services.gemini_client import (
    GeminiError,
    _sanitize_and_parse,
    extract_decision,
)


def test_plain_object():
    assert _sanitize_and_parse('{"a": 1}') == {"a": 1}


def test_strips_markdown_fence_and_prose():
    raw = 'Here you go:\n```json\n{"a": 1, "b": [2, 3]}\n```\nThanks!'
    assert _sanitize_and_parse(raw) == {"a": 1, "b": [2, 3]}


def test_removes_trailing_commas():
    assert _sanitize_and_parse('{"a": 1, "b": [2, 3,],}') == {"a": 1, "b": [2, 3]}


def test_autocloses_truncated_json():
    # Model hit a token limit mid-object; brackets are auto-closed.
    raw = '{"decision": {"Hook": "hi", "list": [1, 2'
    parsed = _sanitize_and_parse(raw)
    assert parsed["decision"]["Hook"] == "hi"
    assert parsed["decision"]["list"] == [1, 2]


def test_extract_decision_unwraps():
    assert extract_decision({"decision": {"x": 1}}) == {"x": 1}


def test_extract_decision_accepts_top_level():
    assert extract_decision({"objective_fit_status": "Strong fit"}) == {
        "objective_fit_status": "Strong fit"
    }


def test_extract_decision_rejects_non_object():
    with pytest.raises(GeminiError):
        extract_decision([1, 2, 3])
