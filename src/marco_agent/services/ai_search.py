from __future__ import annotations

import aiohttp


class AiSearchService:
    def __init__(
        self,
        *,
        endpoint: str | None,
        api_key: str | None,
        index_name: str,
        api_version: str,
    ) -> None:
        self._endpoint = (endpoint or "").rstrip("/")
        self._api_key = (api_key or "").strip()
        self._index_name = index_name.strip()
        self._api_version = api_version.strip()
        self._index_ensured = False

    @property
    def enabled(self) -> bool:
        return bool(self._endpoint and self._api_key and self._index_name and self._api_version)

    async def ensure_index(self) -> bool:
        if not self.enabled:
            return False
        if self._index_ensured:
            return True
        headers = {"api-key": self._api_key, "Content-Type": "application/json"}
        get_url = f"{self._endpoint}/indexes/{self._index_name}?api-version={self._api_version}"
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(get_url, headers=headers) as response:
                if response.status == 200:
                    self._index_ensured = True
                    return True
            payload = {
                "name": self._index_name,
                "fields": [
                    {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
                    {"name": "user_id", "type": "Edm.String", "filterable": True},
                    {"name": "file_id", "type": "Edm.String", "filterable": True},
                    {"name": "file_name", "type": "Edm.String", "searchable": True, "filterable": True},
                    {"name": "chunk_id", "type": "Edm.String", "filterable": True},
                    {"name": "content", "type": "Edm.String", "searchable": True},
                    {"name": "project", "type": "Edm.String", "filterable": True},
                    {"name": "tags", "type": "Collection(Edm.String)", "filterable": True},
                    {"name": "blob_url", "type": "Edm.String", "filterable": True},
                    {
                        "name": "embedding",
                        "type": "Collection(Edm.Single)",
                        "searchable": True,
                        "retrievable": True,
                        "dimensions": 3072,
                        "vectorSearchProfile": "vector-profile",
                    },
                ],
                "vectorSearch": {
                    "algorithms": [{"name": "hnsw-default", "kind": "hnsw"}],
                    "profiles": [{"name": "vector-profile", "algorithm": "hnsw-default"}],
                },
            }
            create_url = f"{self._endpoint}/indexes?api-version={self._api_version}"
            async with session.post(create_url, json=payload, headers=headers) as response:
                if response.status >= 300:
                    return False
                self._index_ensured = True
                return True

    async def upsert_documents(self, *, documents: list[dict]) -> bool:
        if not self.enabled or not documents:
            return False
        if not await self.ensure_index():
            return False
        url = (
            f"{self._endpoint}/indexes/{self._index_name}/docs/index"
            f"?api-version={self._api_version}"
        )
        payload = {"value": [{"@search.action": "upload", **doc} for doc in documents]}
        headers = {"api-key": self._api_key, "Content-Type": "application/json"}
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status >= 300:
                    return False
                body = await response.json()
                return bool(body.get("value"))

    async def vector_search(
        self,
        *,
        user_id: str,
        query_embedding: list[float],
        project: str | None,
        tags: list[str] | None,
        top_k: int,
    ) -> list[dict]:
        if not self.enabled or not query_embedding:
            return []
        if not await self.ensure_index():
            return []
        filters = [f"user_id eq '{_escape_filter(user_id)}'"]
        if project:
            filters.append(f"project eq '{_escape_filter(project)}'")
        if tags:
            tag_filters = " or ".join([f"tags/any(t: t eq '{_escape_filter(tag)}')" for tag in tags if tag.strip()])
            if tag_filters:
                filters.append(f"({tag_filters})")
        filter_expr = " and ".join(filters)
        payload = {
            "count": True,
            "top": max(1, min(int(top_k), 20)),
            "select": "id,file_id,file_name,chunk_id,content,project,tags,blob_url,@search.score",
            "vectorQueries": [
                {
                    "kind": "vector",
                    "vector": query_embedding,
                    "fields": "embedding",
                    "k": max(1, min(int(top_k), 20)),
                }
            ],
            "filter": filter_expr,
        }
        url = (
            f"{self._endpoint}/indexes/{self._index_name}/docs/search"
            f"?api-version={self._api_version}"
        )
        headers = {"api-key": self._api_key, "Content-Type": "application/json"}
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status >= 300:
                    return []
                body = await response.json()
                rows = body.get("value")
                return rows if isinstance(rows, list) else []


def _escape_filter(value: str) -> str:
    return value.replace("'", "''")
