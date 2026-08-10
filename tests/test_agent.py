from agents import agent


class FakeWebSearcher:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def invoke(self, payload):
        self.calls.append(payload)
        return self.payload


def test_general_web_search_filters_dianping_content_and_source_cards(monkeypatch):
    searcher = FakeWebSearcher(
        {
            "results": [
                {
                    "title": "不应暴露的餐厅评价",
                    "url": "https://www.dianping.com/shop/1",
                    "content": "不应复制的评价正文",
                },
                {
                    "title": "大众点评无链接结果",
                    "url": "",
                    "content": "同样不应复制的评价正文",
                },
                {
                    "title": "杭州官方活动",
                    "url": "https://example.test/hangzhou",
                    "content": "公开活动信息",
                },
            ]
        }
    )
    monkeypatch.setattr(agent, "_raw_web_search", searcher)
    agent._reset_runtime_buffers()

    result = agent.perform_web_search("杭州附近有什么活动")
    sources = agent.consume_source_cards()

    assert "不应复制的评价正文" not in result
    assert "同样不应复制的评价正文" not in result
    assert "不应暴露的餐厅评价" not in result
    assert "杭州官方活动" in result
    assert all("dianping.com" not in source.get("url", "") for source in sources)
    assert "Citation ID: web_" in result
    assert sources[0]["evidence_id"].startswith("web_")
    assert sources[0]["source_type"] == "web_search"


def test_general_web_search_invalid_payload_is_explicit_error(monkeypatch):
    monkeypatch.setattr(agent, "_raw_web_search", FakeWebSearcher({"results": "invalid"}))

    result = agent.perform_web_search("杭州附近活动")

    assert "格式无效" in result
