from marco_agent.services.attachment_ingestion import _decode_text_payload


def test_decode_text_payload_plain_text() -> None:
    text = _decode_text_payload(payload=b"hello world", content_type="text/plain")
    assert text == "hello world"


def test_decode_text_payload_unknown_binary_returns_empty() -> None:
    text = _decode_text_payload(payload=b"\x00\x01", content_type="application/octet-stream")
    assert text == ""
