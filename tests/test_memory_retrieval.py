from marco_agent.services.memory_retrieval import _cosine_similarity, _pick_semantic_matches


def test_cosine_similarity_basic() -> None:
    assert round(_cosine_similarity([1.0, 0.0], [1.0, 0.0]), 4) == 1.0
    assert round(_cosine_similarity([1.0, 0.0], [0.0, 1.0]), 4) == 0.0


def test_pick_semantic_matches_filters_by_threshold() -> None:
    query = [1.0, 0.0]
    rows = [
        {"role": "user", "content": "alpha", "embedding": [1.0, 0.0], "created_at": "2026-01-01"},
        {"role": "assistant", "content": "beta", "embedding": [0.2, 0.8], "created_at": "2026-01-02"},
    ]
    matched = _pick_semantic_matches(query_vector=query, candidates=rows, limit=5, threshold=0.7)
    assert len(matched) == 1
    assert matched[0]["content"] == "alpha"
