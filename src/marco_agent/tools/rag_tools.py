from __future__ import annotations

import asyncio
import json
from typing import Any

from marco_agent.services.attachment_ingestion import AttachmentIngestionService
from marco_agent.services.rag_retrieval import RagRetrievalService
from marco_agent.storage.cosmos_files import CosmosFileStore

RAG_TOOL_NAMES = {
    "rag_ingest_text",
    "rag_list_files",
    "rag_search",
    "rag_summarize_file",
    "rag_compare_files",
}


def rag_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "rag_ingest_text",
                "description": "Ingest raw text into file knowledge base with project and tags.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_name": {"type": "string"},
                        "text": {"type": "string"},
                        "project": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["file_name", "text"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rag_list_files",
                "description": "List indexed files for a project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rag_search",
                "description": "Retrieve relevant chunks with citations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "project": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rag_summarize_file",
                "description": "Summarize one indexed file with source chunk citations.",
                "parameters": {
                    "type": "object",
                    "properties": {"file_id": {"type": "string"}},
                    "required": ["file_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rag_compare_files",
                "description": "Compare two indexed files and return grounded differences.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_id_a": {"type": "string"},
                        "file_id_b": {"type": "string"},
                    },
                    "required": ["file_id_a", "file_id_b"],
                    "additionalProperties": False,
                },
            },
        },
    ]


async def execute_rag_tool_call(
    *,
    user_id: str,
    tool_name: str,
    arguments_json: str,
    file_store: CosmosFileStore,
    attachment_ingestion: AttachmentIngestionService,
    rag_retrieval: RagRetrievalService,
    reasoning_deployment: str,
) -> dict[str, Any]:
    args = _load_tool_args(arguments_json)
    if tool_name not in RAG_TOOL_NAMES:
        return {"ok": False, "error": f"Unknown RAG tool '{tool_name}'."}
    if not file_store.enabled:
        return {"ok": False, "error": "RAG file store unavailable. Configure Cosmos DB."}

    try:
        if tool_name == "rag_ingest_text":
            file_name = str(args.get("file_name", "")).strip()
            text = str(args.get("text", "")).strip()
            if not file_name or not text:
                return {"ok": False, "error": "file_name and text are required."}
            return await attachment_ingestion.ingest_text(
                user_id=user_id,
                file_name=file_name,
                text=text,
                project=_as_optional_str(args.get("project")),
                tags=_as_str_list(args.get("tags")),
            )
        if tool_name == "rag_list_files":
            rows = await asyncio.to_thread(
                file_store.list_files,
                user_id=user_id,
                project=_as_optional_str(args.get("project")),
                limit=max(1, min(int(args.get("limit", 20)), 50)),
            )
            return {"ok": True, "files": rows, "count": len(rows)}
        if tool_name == "rag_search":
            query = str(args.get("query", "")).strip()
            if not query:
                return {"ok": False, "error": "query is required."}
            return await rag_retrieval.retrieve(
                user_id=user_id,
                query=query,
                project=_as_optional_str(args.get("project")),
                tags=_as_str_list(args.get("tags")),
                top_k=max(1, min(int(args.get("top_k", 5)), 10)),
            )
        if tool_name == "rag_summarize_file":
            file_id = str(args.get("file_id", "")).strip()
            if not file_id:
                return {"ok": False, "error": "file_id is required."}
            return await rag_retrieval.summarize_file(
                user_id=user_id,
                file_id=file_id,
                deployment=reasoning_deployment,
            )
        if tool_name == "rag_compare_files":
            file_id_a = str(args.get("file_id_a", "")).strip()
            file_id_b = str(args.get("file_id_b", "")).strip()
            if not file_id_a or not file_id_b:
                return {"ok": False, "error": "file_id_a and file_id_b are required."}
            return await rag_retrieval.compare_files(
                user_id=user_id,
                file_id_a=file_id_a,
                file_id_b=file_id_b,
                deployment=reasoning_deployment,
            )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": f"Unhandled RAG tool '{tool_name}'."}


def _load_tool_args(arguments_json: str) -> dict[str, Any]:
    raw = (arguments_json or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
