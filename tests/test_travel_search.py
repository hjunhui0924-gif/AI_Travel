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
    assert result.status == "disabled"
    assert result.candidates == []
    assert searcher.calls == []


def test_enabled_search_preserves_source_and_marks_discovery_only():
    searcher = FakeSearcher()
    result = discover_travel_places("杭州", search_enabled=True, searcher=searcher)

    assert result.attempted is True
    assert result.status == "success"
    assert len(searcher.calls) == 2
    assert result.candidates[0]["source_id"].startswith("web_")
    assert result.sources[0]["reliability"] == "discovery_only"
    assert "rating" not in result.sources[0]["supports"]
    assert result.sources[0]["snippet"] == ""


def test_enabled_search_reports_result_count_in_activity_trace():
    activities = []
    result = discover_travel_places(
        "杭州",
        search_enabled=True,
        searcher=FakeSearcher(),
        activity_logger=lambda stage, title, detail, state="completed": activities.append(
            {"stage": stage, "title": title, "detail": detail, "state": state}
        ),
    )

    assert result.status == "success"
    returned = [item for item in activities if item["title"] == "搜索资料已返回"]
    assert returned
    assert "返回 1 条" in returned[0]["detail"]
    assert "保留 1 条可追溯来源" in returned[0]["detail"]


def test_search_failure_is_returned_without_fake_candidates():
    result = discover_travel_places("杭州", search_enabled=True, searcher=FakeSearcher(error=TimeoutError("timeout")))

    assert result.attempted is True
    assert result.status == "failed"
    assert result.candidates == []
    assert len(result.errors) == 2


def test_dianping_result_is_not_persisted_or_exposed():
    searcher = FakeSearcher(
        payload={
            "results": [
                {
                    "title": "某餐厅评价正文",
                    "url": "https://www.dianping.com/shop/123",
                    "content": "不应被复制的评价文本",
                }
            ]
        }
    )

    result = discover_travel_places("杭州", search_enabled=True, searcher=searcher)

    assert result.candidates == []
    assert result.sources == []


def test_results_without_valid_urls_are_dropped():
    searcher = FakeSearcher(
        payload={
            "results": [
                {"title": "无链接地点"},
                {"title": "相对链接地点", "url": "www.example.test/place"},
                {"title": "有效地点", "url": "https://example.test/place"},
            ]
        }
    )

    result = discover_travel_places("杭州", search_enabled=True, searcher=searcher)

    assert [item["name_hint"] for item in result.candidates] == ["有效地点"]
    assert all(source["url"] == "https://example.test/place" for source in result.sources)


def test_invalid_search_payload_is_reported_as_failed():
    result = discover_travel_places(
        "杭州",
        search_enabled=True,
        searcher=FakeSearcher(payload={"results": "not-a-list"}),
    )

    assert result.status == "failed"
    assert result.candidates == []
    assert any("格式无效" in error for error in result.errors)
