from strategy_codebot.server.llm_json import extract_json_object


def test_extract_json_object_accepts_raw_object() -> None:
    assert extract_json_object('{"action": "start_auto_chain"}') == {"action": "start_auto_chain"}


def test_extract_json_object_accepts_fenced_json() -> None:
    assert extract_json_object('```json\n{"generalizable": true}\n```') == {"generalizable": True}


def test_extract_json_object_accepts_text_around_object_with_braces_in_string() -> None:
    assert extract_json_object('Decision: {"reason": "keep {risk} text", "confidence": "high"} done') == {
        "reason": "keep {risk} text",
        "confidence": "high",
    }


def test_extract_json_object_rejects_invalid_or_non_object_json() -> None:
    assert extract_json_object("not json") is None
    assert extract_json_object("[1, 2, 3]") is None
