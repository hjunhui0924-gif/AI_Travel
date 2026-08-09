from services.travel_search import discover_travel_places


class FakeSearcher:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {"results": [{"title": "西湖夜游", "url": "https://example.test/xihu", "content": "近期热门推荐"}]}
        self.error = error
        self.calls = []

    def invoke(self, payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.payload


def test_search_disabled_never_constructs_or_invokes_searcher():
    searcher = FakeSearcher()
    result = discover_travel_places("杭州", search_enabled=False, searcher=searcher)

    assert result.attempted is False
    assert result.candidates == []
    assert searcher.calls == []


def test_enabled_search_preserves_source_and_marks_discovery_only():
    searcher = FakeSearcher()
    result = discover_travel_places("杭州", search_enabled=True, searcher=searcher)

    assert result.attempted is True
    assert len(searcher.calls) == 2
    assert result.candidates[0]["source_id"] == "web_001"
    assert result.sources[0]["reliability"] == "discovery_only"
    assert "rating" not in result.sources[0]["supports"]


def test_search_failure_is_returned_without_fake_candidates():
    result = discover_travel_places("杭州", search_enabled=True, searcher=FakeSearcher(error=TimeoutError("timeout")))

    assert result.attempted is True
    assert result.candidates == []
    assert len(result.errors) == 2

