from marco_agent.services.file_rag import _chunk_text, parse_project_and_tags


def test_parse_project_and_tags() -> None:
    project, tags = parse_project_and_tags("please ingest project:alpha tags:design, roadmap,ml")
    assert project == "alpha"
    assert tags == ["design", "roadmap", "ml"]


def test_parse_project_defaults() -> None:
    project, tags = parse_project_and_tags("hello world")
    assert project == "default"
    assert tags == []


def test_chunk_text_creates_overlapping_chunks() -> None:
    text = "a" * 2600
    chunks = _chunk_text(text)
    assert len(chunks) >= 2
    assert len(chunks[0]) <= 1200
    assert chunks[0][-200:] == chunks[1][:200]
