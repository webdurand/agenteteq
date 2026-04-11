"""Tests for response_utils: extract_final_response, split_whatsapp_messages, parse_interactive_elements."""
from types import SimpleNamespace

import pytest

from src.agent.response_utils import (
    extract_final_response,
    parse_interactive_elements,
    split_whatsapp_messages,
)


# ---------------------------------------------------------------------------
# Helpers — mock objects simulating Agno response structures
# ---------------------------------------------------------------------------


def _make_message(role="assistant", content=None, tool_calls=None, reasoning_content=None):
    msg = SimpleNamespace()
    msg.role = role
    msg.content = content
    msg.tool_calls = tool_calls
    msg.reasoning_content = reasoning_content
    return msg


def _make_response(content=None, messages=None, reasoning_content=None):
    resp = SimpleNamespace()
    resp.content = content
    resp.messages = messages
    resp.reasoning_content = reasoning_content
    return resp


# ---------------------------------------------------------------------------
# extract_final_response
# ---------------------------------------------------------------------------


class TestExtractFinalResponse:
    def test_simple_single_message(self):
        resp = _make_response(
            content="fallback content",
            messages=[_make_message(content="Hello from the agent!")]
        )
        assert extract_final_response(resp) == "Hello from the agent!"

    def test_returns_last_assistant_message_without_tool_calls(self):
        resp = _make_response(
            content="fallback",
            messages=[
                _make_message(content="Vou agendar...", tool_calls=["schedule_tool"]),
                _make_message(content="Pronto, agendei!"),
            ]
        )
        assert extract_final_response(resp) == "Pronto, agendei!"

    def test_ignores_intermediate_tool_call_messages(self):
        resp = _make_response(
            content="fallback",
            messages=[
                _make_message(content="Tentando...", tool_calls=["tool1"]),
                _make_message(content="Tentando de novo...", tool_calls=["tool2"]),
                _make_message(content="Feito!"),
            ]
        )
        assert extract_final_response(resp) == "Feito!"

    def test_falls_back_to_last_with_content_if_all_have_tool_calls(self):
        resp = _make_response(
            content="fallback",
            messages=[
                _make_message(content="Step 1", tool_calls=["t1"]),
                _make_message(content="Step 2", tool_calls=["t2"]),
            ]
        )
        # Should return the last one with content (Step 2)
        assert extract_final_response(resp) == "Step 2"

    def test_falls_back_to_response_content(self):
        resp = _make_response(content="Direct content", messages=[])
        assert extract_final_response(resp) == "Direct content"

    def test_falls_back_to_reasoning_content(self):
        resp = _make_response(content="", messages=[], reasoning_content="Thinking deeply...")
        assert extract_final_response(resp) == "Thinking deeply..."

    def test_empty_response(self):
        resp = _make_response(content="", messages=[], reasoning_content="")
        assert extract_final_response(resp) == ""

    def test_none_content(self):
        resp = _make_response(content=None, messages=None)
        assert extract_final_response(resp) == ""

    def test_ignores_non_assistant_messages(self):
        resp = _make_response(
            content="fallback",
            messages=[
                _make_message(role="user", content="My question"),
                _make_message(role="tool", content="Tool result"),
                _make_message(role="assistant", content="Agent answer"),
            ]
        )
        assert extract_final_response(resp) == "Agent answer"

    def test_skips_messages_with_no_content(self):
        resp = _make_response(
            content="fallback",
            messages=[
                _make_message(content=None),
                _make_message(content=""),
                _make_message(content="Final answer"),
            ]
        )
        assert extract_final_response(resp) == "Final answer"


# ---------------------------------------------------------------------------
# split_whatsapp_messages
# ---------------------------------------------------------------------------


class TestSplitWhatsappMessages:
    def test_short_message_not_split(self):
        text = "Hello, this is a short message."
        result = split_whatsapp_messages(text)
        assert result == [text]

    def test_empty_text_returns_empty_list(self):
        assert split_whatsapp_messages("") == []
        assert split_whatsapp_messages(None) == []

    def test_exact_max_length_not_split(self):
        text = "a" * 1500
        result = split_whatsapp_messages(text, max_length=1500)
        assert len(result) == 1

    def test_splits_at_paragraph_boundary(self):
        para1 = "A" * 800
        para2 = "B" * 800
        text = f"{para1}\n\n{para2}"
        result = split_whatsapp_messages(text, max_length=1000)
        assert len(result) == 2
        assert result[0].strip() == para1
        assert result[1].strip() == para2

    def test_splits_very_long_paragraph(self):
        """A single paragraph longer than max_length should be chunk-split."""
        long_para = "X" * 3000
        result = split_whatsapp_messages(long_para, max_length=1500)
        assert len(result) >= 2
        # All content should be preserved
        joined = "".join(result)
        assert len(joined) == 3000

    def test_multiple_paragraphs_fit_in_one_chunk(self):
        para1 = "Short paragraph one."
        para2 = "Short paragraph two."
        text = f"{para1}\n\n{para2}"
        result = split_whatsapp_messages(text, max_length=1500)
        assert len(result) == 1
        assert para1 in result[0]
        assert para2 in result[0]

    def test_custom_max_length(self):
        text = "A" * 50 + "\n\n" + "B" * 50
        result = split_whatsapp_messages(text, max_length=60)
        assert len(result) == 2

    def test_preserves_content(self):
        paragraphs = [f"Paragraph {i}: " + "x" * 100 for i in range(10)]
        text = "\n\n".join(paragraphs)
        result = split_whatsapp_messages(text, max_length=500)
        reconstructed = "\n\n".join(result)
        for p in paragraphs:
            assert p.strip() in reconstructed

    @pytest.mark.parametrize("max_len", [100, 500, 1000, 1500, 2000])
    def test_no_chunk_exceeds_max_length(self, max_len):
        text = "\n\n".join(["Word " * 50 for _ in range(20)])
        result = split_whatsapp_messages(text, max_length=max_len)
        for chunk in result:
            assert len(chunk) <= max_len


# ---------------------------------------------------------------------------
# parse_interactive_elements — BUTTONS
# ---------------------------------------------------------------------------


class TestParseButtons:
    def test_no_markup_returns_original(self):
        text = "Just a normal message."
        result = parse_interactive_elements(text)
        assert result["body"] == text
        assert result["buttons"] is None
        assert result["list"] is None

    def test_parses_buttons(self):
        text = (
            "Choose an option:\n"
            "[BUTTONS]\n"
            "Option A\n"
            "Option B\n"
            "Option C\n"
            "[/BUTTONS]"
        )
        result = parse_interactive_elements(text)
        assert result["buttons"] is not None
        assert len(result["buttons"]) == 3
        assert result["buttons"][0]["title"] == "Option A"
        assert result["buttons"][1]["title"] == "Option B"
        assert result["buttons"][2]["title"] == "Option C"
        assert "[BUTTONS]" not in result["body"]
        assert "[/BUTTONS]" not in result["body"]

    def test_buttons_have_ids(self):
        text = "[BUTTONS]\nYes\nNo\n[/BUTTONS]"
        result = parse_interactive_elements(text)
        assert result["buttons"][0]["id"] == "btn_1"
        assert result["buttons"][1]["id"] == "btn_2"

    def test_buttons_truncated_to_three(self):
        text = "[BUTTONS]\nA\nB\nC\nD\nE\n[/BUTTONS]"
        result = parse_interactive_elements(text)
        assert len(result["buttons"]) == 3

    def test_button_title_truncated_to_20_chars(self):
        long_title = "This is a very long button title exceeding twenty characters"
        text = f"[BUTTONS]\n{long_title}\n[/BUTTONS]"
        result = parse_interactive_elements(text)
        assert len(result["buttons"][0]["title"]) <= 20

    def test_body_text_preserved_before_buttons(self):
        text = "Pick your choice:\n[BUTTONS]\nRed\nBlue\n[/BUTTONS]"
        result = parse_interactive_elements(text)
        assert "Pick your choice" in result["body"]

    def test_body_text_preserved_after_buttons(self):
        text = "[BUTTONS]\nRed\nBlue\n[/BUTTONS]\nThank you!"
        result = parse_interactive_elements(text)
        assert "Thank you!" in result["body"]


# ---------------------------------------------------------------------------
# parse_interactive_elements — LIST
# ---------------------------------------------------------------------------


class TestParseList:
    def test_parses_list(self):
        text = (
            "Here are your options:\n"
            "[LIST Menu]\n"
            "Item 1 - Description one\n"
            "Item 2 - Description two\n"
            "[/LIST]"
        )
        result = parse_interactive_elements(text)
        assert result["list"] is not None
        assert result["list"]["button_text"] == "Menu"
        rows = result["list"]["sections"][0]["rows"]
        assert len(rows) == 2
        assert rows[0]["title"] == "Item 1"
        assert rows[0]["description"] == "Description one"

    def test_list_default_button_text(self):
        text = "[LIST]\nItem 1\n[/LIST]"
        result = parse_interactive_elements(text)
        assert result["list"]["button_text"] == "Menu"

    def test_list_rows_have_ids(self):
        text = "[LIST]\nRow A\nRow B\nRow C\n[/LIST]"
        result = parse_interactive_elements(text)
        rows = result["list"]["sections"][0]["rows"]
        assert rows[0]["id"] == "row_0"
        assert rows[1]["id"] == "row_1"

    def test_list_em_dash_separator(self):
        text = "[LIST]\nItem \u2014 Description with em dash\n[/LIST]"
        result = parse_interactive_elements(text)
        rows = result["list"]["sections"][0]["rows"]
        # The code uses ' \u2014 ' (em dash with spaces)
        assert rows[0]["title"] == "Item"
        assert "Description" in rows[0]["description"]

    def test_list_body_preserved(self):
        text = "Select from the list:\n[LIST Options]\nA\nB\n[/LIST]\nDone!"
        result = parse_interactive_elements(text)
        assert "Select from the list" in result["body"]
        assert "Done!" in result["body"]
        assert "[LIST" not in result["body"]

    def test_list_title_truncated_to_24(self):
        long_title = "A" * 50
        text = f"[LIST]\n{long_title}\n[/LIST]"
        result = parse_interactive_elements(text)
        rows = result["list"]["sections"][0]["rows"]
        assert len(rows[0]["title"]) <= 24

    def test_list_description_truncated_to_72(self):
        long_desc = "D" * 100
        text = f"[LIST]\nTitle - {long_desc}\n[/LIST]"
        result = parse_interactive_elements(text)
        rows = result["list"]["sections"][0]["rows"]
        assert len(rows[0]["description"]) <= 72

    def test_list_max_10_rows(self):
        lines = "\n".join([f"Row {i}" for i in range(15)])
        text = f"[LIST]\n{lines}\n[/LIST]"
        result = parse_interactive_elements(text)
        rows = result["list"]["sections"][0]["rows"]
        assert len(rows) <= 10

    def test_empty_lines_ignored_in_list(self):
        text = "[LIST]\nA\n\nB\n\n\nC\n[/LIST]"
        result = parse_interactive_elements(text)
        rows = result["list"]["sections"][0]["rows"]
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# parse_interactive_elements — priority (buttons > list)
# ---------------------------------------------------------------------------


class TestParseInteractivePriority:
    def test_buttons_take_priority_over_list(self):
        """If both BUTTONS and LIST are present, BUTTONS regex matches first."""
        text = (
            "[BUTTONS]\nA\nB\n[/BUTTONS]\n"
            "[LIST]\nX\nY\n[/LIST]"
        )
        result = parse_interactive_elements(text)
        # BUTTONS match comes first so list is not parsed
        assert result["buttons"] is not None
        assert result["list"] is None

    def test_case_insensitive_tags(self):
        text = "[buttons]\nYes\nNo\n[/buttons]"
        result = parse_interactive_elements(text)
        assert result["buttons"] is not None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_parse_empty_string(self):
        result = parse_interactive_elements("")
        assert result["body"] == ""
        assert result["buttons"] is None
        assert result["list"] is None

    def test_parse_no_buttons_content(self):
        text = "[BUTTONS]\n[/BUTTONS]"
        result = parse_interactive_elements(text)
        # No lines to parse → no buttons
        assert result["buttons"] is None

    def test_split_single_char_message(self):
        assert split_whatsapp_messages("X") == ["X"]

    def test_extract_with_no_messages_attribute(self):
        resp = SimpleNamespace(content="Direct", messages=None, reasoning_content=None)
        assert extract_final_response(resp) == "Direct"
