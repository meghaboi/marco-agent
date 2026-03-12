import asyncio

from marco_agent.tools.news_tools import NEWS_TOOL_NAMES, execute_news_tool_call, news_tool_definitions


class StubDigestStore:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.preferences = {}
        self.opens = []

    def upsert_preferences(self, *, user_id, timezone, digest_time_local, categories):
        row = {
            "user_id": user_id,
            "timezone": timezone,
            "digest_time_local": digest_time_local,
            "categories": categories,
        }
        self.preferences[user_id] = row
        return row

    def get_preferences(self, *, user_id):
        return self.preferences.get(user_id)

    def list_recent_digests(self, *, user_id, limit=5):
        _ = user_id
        _ = limit
        return [{"digest_id": "d1", "created_at": "2026-03-12T00:00:00+00:00", "categories": ["ai"]}]

    def track_open(self, *, user_id, digest_id, source):
        self.opens.append((user_id, digest_id, source))

    def digest_open_rate(self, *, user_id, digest_id):
        _ = user_id
        _ = digest_id
        return {"deliveries": 2, "opens": 1}

    def track_delivery(self, *, user_id, digest_id, channel, status):
        _ = (user_id, digest_id, channel, status)


class StubDigestService:
    async def build_and_store_digest(self, *, user_id, deployment, categories, max_items):
        _ = (deployment, max_items)
        return {
            "digest_id": "dg1",
            "summary": "Top updates [1]",
            "items": [{"title": "Story", "source": "Example", "url": "https://example.com"}],
            "categories": categories,
            "created_at": "2026-03-12T00:00:00+00:00",
            "user_id": user_id,
        }

    async def dig_deeper(self, *, user_id, deployment, digest_id, topic):
        _ = (user_id, deployment)
        return {"ok": True, "digest_id": digest_id, "topic": topic, "brief": "Detailed [1]", "sources": []}


def test_news_tool_definitions_include_expected_tools() -> None:
    names = {item["function"]["name"] for item in news_tool_definitions()}
    assert names == NEWS_TOOL_NAMES


def test_execute_preferences_set_and_get() -> None:
    store = StubDigestStore()
    service = StubDigestService()
    set_result = asyncio.run(
        execute_news_tool_call(
            digest_store=store,  # type: ignore[arg-type]
            digest_service=service,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="digest_preferences_set",
            arguments_json='{"digest_time_local":"08:30","timezone":"Asia/Calcutta","categories":["ai","world"]}',
            default_categories=["world"],
            default_max_items=5,
            reasoning_deployment="x",
        )
    )
    assert set_result["ok"] is True
    get_result = asyncio.run(
        execute_news_tool_call(
            digest_store=store,  # type: ignore[arg-type]
            digest_service=service,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="digest_preferences_get",
            arguments_json="{}",
            default_categories=["world"],
            default_max_items=5,
            reasoning_deployment="x",
        )
    )
    assert get_result["ok"] is True
    assert get_result["preferences"]["timezone"] == "Asia/Calcutta"


def test_execute_digest_generate_now_routes_to_service() -> None:
    store = StubDigestStore()
    service = StubDigestService()
    result = asyncio.run(
        execute_news_tool_call(
            digest_store=store,  # type: ignore[arg-type]
            digest_service=service,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="digest_generate_now",
            arguments_json='{"categories":["technology"],"max_items":3}',
            default_categories=["world"],
            default_max_items=5,
            reasoning_deployment="reasoning-x",
        )
    )
    assert result["ok"] is True
    assert result["digest"]["digest_id"] == "dg1"


def test_execute_preferences_set_rejects_bad_timezone() -> None:
    store = StubDigestStore()
    service = StubDigestService()
    result = asyncio.run(
        execute_news_tool_call(
            digest_store=store,  # type: ignore[arg-type]
            digest_service=service,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="digest_preferences_set",
            arguments_json='{"digest_time_local":"08:30","timezone":"Mars/Olympus","categories":["ai"]}',
            default_categories=["world"],
            default_max_items=5,
            reasoning_deployment="x",
        )
    )
    assert result["ok"] is False
    assert "timezone" in result["error"].lower()
