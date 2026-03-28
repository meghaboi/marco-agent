import asyncio

from marco_agent.tools.rag_tools import RAG_TOOL_NAMES, execute_rag_tool_call, rag_tool_definitions


class StubFileStore:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.rows = [
            {"file_id": "f1", "file_name": "notes.txt", "project": "proj", "tags": ["a"], "created_at": "2026-03-01"}
        ]

    def list_files(self, *, user_id, project=None, limit=20):
        _ = user_id
        filtered = self.rows
        if project:
            filtered = [row for row in filtered if row.get("project") == project]
        return filtered[:limit]


class StubIngestion:
    async def ingest_text(self, *, user_id, file_name, text, project=None, tags=None):
        _ = (user_id, project, tags)
        return {"ok": True, "file_id": "f2", "file_name": file_name, "chunk_count": 2, "indexed_count": 2, "text": text}


class StubRetrieval:
    async def retrieve(self, *, user_id, query, project, tags, top_k):
        _ = (user_id, project, tags, top_k)
        return {
            "ok": True,
            "query": query,
            "citations": [{"file_id": "f1", "file_name": "notes.txt", "chunk_id": "c1", "snippet": "hello"}],
            "count": 1,
        }

    async def summarize_file(self, *, user_id, file_id, deployment):
        _ = (user_id, deployment)
        return {"ok": True, "file_id": file_id, "summary": "Summary [1]"}

    async def compare_files(self, *, user_id, file_id_a, file_id_b, deployment):
        _ = (user_id, deployment)
        return {"ok": True, "file_id_a": file_id_a, "file_id_b": file_id_b, "comparison": "Diff [1]"}


def test_rag_tool_definitions_include_expected_tools() -> None:
    names = {item["function"]["name"] for item in rag_tool_definitions()}
    assert names == RAG_TOOL_NAMES


def test_execute_rag_list_and_search() -> None:
    file_store = StubFileStore()
    result = asyncio.run(
        execute_rag_tool_call(
            user_id="u1",
            tool_name="rag_list_files",
            arguments_json='{"project":"proj"}',
            file_store=file_store,  # type: ignore[arg-type]
            attachment_ingestion=StubIngestion(),  # type: ignore[arg-type]
            rag_retrieval=StubRetrieval(),  # type: ignore[arg-type]
            reasoning_deployment="x",
        )
    )
    assert result["ok"] is True
    assert result["count"] == 1

    search = asyncio.run(
        execute_rag_tool_call(
            user_id="u1",
            tool_name="rag_search",
            arguments_json='{"query":"hello"}',
            file_store=file_store,  # type: ignore[arg-type]
            attachment_ingestion=StubIngestion(),  # type: ignore[arg-type]
            rag_retrieval=StubRetrieval(),  # type: ignore[arg-type]
            reasoning_deployment="x",
        )
    )
    assert search["ok"] is True
    assert search["count"] == 1


def test_execute_rag_summarize_and_compare() -> None:
    file_store = StubFileStore()
    summarize = asyncio.run(
        execute_rag_tool_call(
            user_id="u1",
            tool_name="rag_summarize_file",
            arguments_json='{"file_id":"f1"}',
            file_store=file_store,  # type: ignore[arg-type]
            attachment_ingestion=StubIngestion(),  # type: ignore[arg-type]
            rag_retrieval=StubRetrieval(),  # type: ignore[arg-type]
            reasoning_deployment="x",
        )
    )
    assert summarize["ok"] is True

    compare = asyncio.run(
        execute_rag_tool_call(
            user_id="u1",
            tool_name="rag_compare_files",
            arguments_json='{"file_id_a":"f1","file_id_b":"f2"}',
            file_store=file_store,  # type: ignore[arg-type]
            attachment_ingestion=StubIngestion(),  # type: ignore[arg-type]
            rag_retrieval=StubRetrieval(),  # type: ignore[arg-type]
            reasoning_deployment="x",
        )
    )
    assert compare["ok"] is True
