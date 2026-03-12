from __future__ import annotations

import logging
from typing import Any

try:
    from azure.core.credentials import AzureKeyCredential
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SearchableField,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )
except ImportError:  # pragma: no cover - optional dependency for non-RAG runtimes
    AzureKeyCredential = None
    DefaultAzureCredential = None
    SearchClient = None
    SearchIndexClient = None

LOGGER = logging.getLogger(__name__)


class AzureSearchChunkStore:
    def __init__(
        self,
        *,
        endpoint: str | None,
        index_name: str,
        api_key: str | None = None,
        embedding_dimensions: int = 3072,
    ) -> None:
        self._enabled = bool(endpoint and index_name)
        self._client: SearchClient | None = None
        if SearchClient is None or SearchIndexClient is None:
            self._enabled = False
            LOGGER.warning("azure-search-documents not installed; chunk index disabled.")
            return

        if not self._enabled:
            LOGGER.warning("Azure AI Search not configured; chunk index disabled.")
            return

        if api_key and AzureKeyCredential is not None:
            credential = AzureKeyCredential(api_key)
        elif DefaultAzureCredential is not None:
            credential = DefaultAzureCredential()
        else:
            self._enabled = False
            LOGGER.warning("No Azure credential provider available for AI Search; chunk index disabled.")
            return
        assert endpoint
        index_client = SearchIndexClient(endpoint=endpoint, credential=credential)
        self._ensure_index(index_client, index_name=index_name, embedding_dimensions=embedding_dimensions)
        self._client = SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @staticmethod
    def _ensure_index(client: SearchIndexClient, *, index_name: str, embedding_dimensions: int) -> None:
        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="user_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="project_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="file_id", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="filename", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SimpleField(name="blob_url", type=SearchFieldDataType.String),
            SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, filterable=True),
            SimpleField(name="tags", type=SearchFieldDataType.Collection(SearchFieldDataType.String), filterable=True),
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=embedding_dimensions,
                vector_search_profile_name="content-profile",
            ),
        ]
        vector_search = VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw-config")],
            profiles=[VectorSearchProfile(name="content-profile", algorithm_configuration_name="hnsw-config")],
        )
        index = SearchIndex(name=index_name, fields=fields, vector_search=vector_search)
        client.create_or_update_index(index)

    def upsert_chunks(self, *, chunks: list[dict[str, Any]]) -> None:
        if not self._client or not chunks:
            return
        self._client.upload_documents(documents=chunks)

    def vector_search(
        self,
        *,
        vector: list[float],
        user_id: str,
        project_id: str | None,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        if not self._client or not vector:
            return []
        filter_expr = f"user_id eq '{user_id}'"
        if project_id:
            filter_expr += f" and project_id eq '{project_id}'"
        results = self._client.search(
            search_text=None,
            filter=filter_expr,
            vector_queries=[{"kind": "vector", "vector": vector, "fields": "content_vector", "k": top_k}],
            select=["id", "file_id", "filename", "content", "blob_url", "project_id", "tags", "chunk_index"],
        )
        return [dict(item) for item in results]
