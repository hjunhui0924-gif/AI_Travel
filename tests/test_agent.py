from agents import agent
from langchain.messages import AIMessage, AIMessageChunk
from agents.schemas import TravelPlanResponse


class FakeWebSearcher:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def invoke(self, payload):
        self.calls.append(payload)
        return self.payload


def test_model_settings_prefer_deepseek_when_generic_llm_is_not_configured(monkeypatch):
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deekseek.com/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    settings = agent._resolve_model_settings()

    assert settings["model"] == "deepseek-chat"
    assert settings["model_provider"] == "openai"
    assert settings["base_url"] == "https://api.deepseek.com/v1"
    assert settings["api_key"] == "test-deepseek-key"


def test_common_three_day_trip_request_uses_structured_travel_planner():
    assert agent._is_travel_query("请规划杭州三日游，包含西湖和灵隐寺。", []) is True


def test_model_fallback_extracts_unmatched_travel_request(monkeypatch):
    class FakeModel:
        def invoke(self, messages):
            return AIMessage(
                content=(
                    '{"is_travel_request": true, "intent": "trip_plan", '
                    '"origin": "", "destination": "江苏", '
                    '"destination_cities": [], "start_date": "", "days": 5, '
                    '"travelers": 2, "travel_mode": "", "preferences": ["美食"]}'
                )
            )

    monkeypatch.setattr(agent, "model", FakeModel())
    agent._reset_runtime_buffers()

    hint = agent._extract_travel_intent_with_model("周末带爸妈去江苏，安排轻松一点", [])

    assert hint == {
        "is_travel_request": True,
        "intent": "trip_plan",
        "origin": "",
        "destination": "江苏",
        "destination_cities": [],
        "start_date": "",
        "days": 5,
        "travelers": 2,
        "travel_mode": "",
        "preferences": ["美食"],
    }


def test_model_fallback_classifies_unmatched_non_travel_message(monkeypatch):
    calls = []

    class FakeModel:
        def invoke(self, messages):
            calls.append(messages)
            assert "不是通用聊天助手" in messages[0].content
            assert "只输出一个 JSON 对象" in messages[0].content
            return AIMessage(content='{"is_travel_request": false}')

    monkeypatch.setattr(agent, "model", FakeModel())

    decision = agent._extract_travel_intent_with_model("帮我写一封邮件", [])

    assert calls
    assert decision is not None
    assert decision["is_travel_request"] is False


def test_non_travel_fallback_returns_refusal_instead_of_generic_agent(monkeypatch):
    class FakeModel:
        def invoke(self, messages):
            return AIMessage(content='{"is_travel_request": false}')

    class UnexpectedAgent:
        def stream(self, *args, **kwargs):
            raise AssertionError("generic agent must not receive an unmatched message")

    monkeypatch.setattr(agent, "model", FakeModel())
    monkeypatch.setattr(agent, "agent_without_search", UnexpectedAgent())
    monkeypatch.setattr(agent, "get_current_plan", lambda thread_id, user_id=None: None)
    monkeypatch.setattr(agent, "_get_pending_travel_query", lambda thread_id, user_id=None: None)

    chunks = list(agent.stream_chat("帮我写一封邮件", "guest_scope_test", False, []))
    rendered = "".join(
        item.get("text", "")
        for chunk, _metadata in chunks
        if isinstance(chunk, AIMessageChunk)
        for item in (chunk.content if isinstance(chunk.content, list) else [])
        if isinstance(item, dict)
    )

    assert "只支持旅行相关" in rendered


def test_travel_response_yields_incremental_text_chunks(monkeypatch):
    rendered = "第一段旅行建议。\n\n第二段路线说明。\n\n第三段注意事项。"

    monkeypatch.setattr(
        agent,
        "plan_travel",
        lambda *args, **kwargs: TravelPlanResponse(intent="trip_plan", summary="ok"),
    )
    monkeypatch.setattr(agent, "render_travel_response", lambda response: rendered)
    monkeypatch.setattr(agent, "TRAVEL_STREAM_DELAY_SECONDS", 0, raising=False)

    chunks = list(agent._stream_travel_response("去杭州玩三天", "", False, []))
    text_parts = [
        item.get("text", "")
        for chunk, _metadata in chunks
        if isinstance(chunk, AIMessageChunk)
        for item in (chunk.content if isinstance(chunk.content, list) else [])
        if isinstance(item, dict)
    ]

    assert len(text_parts) > 1
    assert "".join(text_parts) == rendered


def test_pending_clarification_is_reused_for_a_follow_up_turn(monkeypatch):
    pending_query = {"raw_text": "江苏五日游", "destination": "江苏", "days": 5}
    assistant_content = "请先选择城市。" + agent.encode_assistant_metadata(
        [],
        [],
        clarification={"code": "destination_cities", "prompt": "请选择城市", "options": []},
        pending_query=pending_query,
    )
    monkeypatch.setattr(
        agent,
        "list_travel_turns",
        lambda thread_id, user_id=None: [{"role": "assistant", "content": assistant_content}],
    )

    assert agent._get_pending_travel_query("guest_pending_test") == pending_query


def test_unclassified_travel_request_is_routed_to_the_travel_orchestrator(monkeypatch):
    captured = {}

    monkeypatch.setattr(agent, "_is_travel_query", lambda message, attachments: False)
    monkeypatch.setattr(agent, "_looks_like_unclassified_travel_query", lambda message, attachments: True)
    monkeypatch.setattr(
        agent,
        "_extract_travel_intent_with_model",
        lambda message, attachments, context=None: {
            "is_travel_request": True,
            "intent": "trip_plan",
            "destination": "江苏",
        },
    )
    monkeypatch.setattr(agent, "get_current_plan", lambda thread_id, user_id=None: None)

    def fake_stream(*args, **kwargs):
        captured.update(kwargs)
        return iter(())

    monkeypatch.setattr(agent, "_stream_travel_response", fake_stream)

    list(agent.stream_chat("周末带家人去江苏", "guest_fallback_test", False, []))

    assert captured["extraction_hint"]["destination"] == "江苏"


def test_pending_travel_clarification_does_not_capture_non_travel_follow_up(monkeypatch):
    class FakeModel:
        def invoke(self, messages):
            return AIMessage(content='{"is_travel_request": false}')

    class UnexpectedTravelPlanner:
        def __call__(self, *args, **kwargs):
            raise AssertionError("an unrelated follow-up must not enter travel planning")

    monkeypatch.setattr(agent, "model", FakeModel())
    monkeypatch.setattr(agent, "get_current_plan", lambda thread_id, user_id=None: None)
    monkeypatch.setattr(
        agent,
        "_get_pending_travel_query",
        lambda thread_id, user_id=None: {
            "raw_text": "江苏五日游",
            "destination": "江苏",
            "days": 5,
        },
    )
    monkeypatch.setattr(agent, "_stream_travel_response", UnexpectedTravelPlanner())

    chunks = list(agent.stream_chat("帮我写一封邮件", "guest_pending_scope_test", False, []))
    rendered = "".join(
        item.get("text", "")
        for chunk, _metadata in chunks
        if isinstance(chunk, AIMessageChunk)
        for item in (chunk.content if isinstance(chunk.content, list) else [])
        if isinstance(item, dict)
    )

    assert "只支持旅行相关" in rendered


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
