import asyncio

from marco_agent.services.news_digest import NewsDigestService, NewsItem


class StubAiClient:
    async def chat(self, **kwargs):
        _ = kwargs
        return "summary"


class StubDigestStore:
    def __init__(self) -> None:
        self.saved = None

    def save_digest(self, *, user_id, summary, items, categories):
        self.saved = {
            "user_id": user_id,
            "summary": summary,
            "items": items,
            "categories": categories,
        }
        return {"digest_id": "dg1", "created_at": "2026-03-12T00:00:00+00:00"}


def test_build_and_store_digest_serializes_slot_dataclass_items() -> None:
    store = StubDigestStore()
    service = NewsDigestService(
        ai_client=StubAiClient(),  # type: ignore[arg-type]
        digest_store=store,  # type: ignore[arg-type]
        rss_url_template="https://example.com?q={query}",
    )

    async def fake_fetch_news(*, categories, max_items):
        _ = (categories, max_items)
        return [
            NewsItem(
                title="Headline",
                url="https://example.com/story",
                source="Example",
                published_at="Wed, 12 Mar 2026 00:00:00 GMT",
                category="technology",
            )
        ]

    service.fetch_news = fake_fetch_news  # type: ignore[method-assign]

    result = asyncio.run(
        service.build_and_store_digest(
            user_id="u1",
            deployment="reasoning-x",
            categories=["technology"],
            max_items=3,
        )
    )

    assert result["items"] == [
        {
            "title": "Headline",
            "url": "https://example.com/story",
            "source": "Example",
            "published_at": "Wed, 12 Mar 2026 00:00:00 GMT",
            "category": "technology",
        }
    ]
    assert store.saved is not None
    assert store.saved["items"] == result["items"]
