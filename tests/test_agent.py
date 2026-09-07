from threading import Event
from uuid import uuid4

from agents import agent
from langchain.messages import AIMessage, AIMessageChunk
from agents.schemas import TransportOption, TransportPage, TransportQueryPage, TravelPlan, TravelPlanResponse
from services.travel_store import delete_travel_thread, ensure_guest_access


def test_model_settings_prefer_qwen_when_dashscope_is_configured(monkeypatch):
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_BASE_URL", "DASHSCOPE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deekseek.com/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    settings = agent._resolve_model_settings()

    assert settings["model"] == "qwen3.7-flash"
    assert settings["model_provider"] == "openai"
    assert settings["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert settings["api_key"] == "test-dashscope-key"
    assert settings["extra_body"] == {"enable_thinking": False}


def test_model_settings_fall_back_to_deepseek_without_dashscope(monkeypatch):
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_BASE_URL", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deekseek.com/v1")

    settings = agent._resolve_model_settings()

    assert settings["model"] == "deepseek-chat"
    assert settings["base_url"] == "https://api.deepseek.com/v1"
    assert settings["api_key"] == "test-deepseek-key"


def test_common_three_day_trip_request_uses_structured_travel_planner():
    assert agent._is_travel_query("请规划杭州三日游，包含西湖和灵隐寺。", []) is True


def test_negated_travel_word_does_not_bypass_scope_classifier(monkeypatch):
    class FakeModel:
        def invoke(self, messages):
            return AIMessage(content='{"is_travel_request": false}')

    monkeypatch.setattr(agent, "model", FakeModel())
    monkeypatch.setattr(agent, "get_current_plan", lambda thread_id, user_id=None: None)

    message = "请帮我写一封关于人工智能的工作邮件，不涉及旅行"
    assert agent._is_travel_query(message, []) is False
    chunks = list(agent.stream_chat(message, "guest_negated_scope_test", False, []))
    rendered = "".join(
        item.get("text", "")
        for chunk, _metadata in chunks
        if isinstance(chunk, AIMessageChunk)
        for item in (chunk.content if isinstance(chunk.content, list) else [])
        if isinstance(item, dict)
    )

    assert "只支持旅行相关" in rendered

def test_keyword_route_handles_explicit_transport_without_scope_model(monkeypatch):
    captured = {}

    def unexpected_classifier(*_args, **_kwargs):
        raise AssertionError("explicit travel keywords should not call the scope classifier")

    monkeypatch.setattr(agent, "_extract_travel_intent_with_model", unexpected_classifier)
    monkeypatch.setattr(agent, "get_current_plan", lambda thread_id, user_id=None: None)

    def fake_stream(*args, **kwargs):
        captured.update(kwargs)
        return iter(())

    monkeypatch.setattr(agent, "_stream_travel_response", fake_stream)

    list(agent.stream_chat("明天从上海到杭州查高铁", "keyword_route_test", False, []))

    assert captured.get("extraction_hint") is None


def test_unmatched_message_uses_scope_model_as_fallback(monkeypatch):
    calls = []

    class FakeModel:
        def invoke(self, messages):
            calls.append(messages)
            return AIMessage(
                content=(
                    '{"is_travel_request": true, "intent": "nearby_explore", '
                    '"destination": "杭州", "destination_cities": [], '
                    '"origin": "", "start_date": "", "days": null, '
                    '"travelers": null, "travel_mode": "", "preferences": []}'
                )
            )

    captured = {}
    monkeypatch.setattr(agent, "model", FakeModel())
    monkeypatch.setattr(agent, "get_current_plan", lambda thread_id, user_id=None: None)

    def fake_stream(*args, **kwargs):
        captured.update(kwargs)
        return iter(())

    monkeypatch.setattr(agent, "_stream_travel_response", fake_stream)

    list(agent.stream_chat("周末想找个适合散步的地方", "model_route_test", False, []))

    assert calls
    assert captured["extraction_hint"]["intent"] == "nearby_explore"


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

    monkeypatch.setattr(agent, "model", FakeModel())
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


def test_sync_activity_stream_forwards_each_event_before_operation_finishes():
    agent._reset_runtime_buffers()
    release = Event()

    def operation(activity_logger):
        activity_logger("progress", "第一步已完成", "", origin="model")
        assert release.wait(1)
        activity_logger("progress", "第二步已完成", "", origin="provider")
        return "operation-result"

    stream = agent._stream_sync_with_activities(operation)
    next(stream)
    first = agent.consume_activity_log()
    assert [item["title"] for item in first] == ["第一步已完成"]

    release.set()
    list(stream)
    second = agent.consume_activity_log()
    assert [item["title"] for item in second] == ["第二步已完成"]
    assert second[0]["origin"] == "provider"


def test_travel_work_progress_is_persisted_after_live_events(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    ensure_guest_access(thread_id)
    query = agent.prepare_travel_query("杭州有哪些适合散步的地方", [])
    supervised = agent.TravelSupervisorResult(
        query=query,
        decision="answer",
        answer="已找到适合散步的地点。",
        decision_reason="用户只要求查询地点。",
    )

    monkeypatch.setattr(agent, "get_current_plan", lambda thread_id, user_id=None: None)
    def fake_supervisor(*args, **kwargs):
        kwargs["activity_logger"](
            "progress",
            "我先查询目的地的已验证地点。",
            "",
            "completed",
            origin="model",
        )
        return supervised

    monkeypatch.setattr(agent, "run_travel_supervisor", fake_supervisor)
    monkeypatch.setattr(
        agent,
        "plan_travel",
        lambda *args, **kwargs: TravelPlanResponse(
            intent="nearby_explore",
            summary="已找到适合散步的地点。",
            decision="answer",
        ),
    )
    monkeypatch.setattr(agent, "render_travel_response", lambda response: response.summary)

    try:
        list(agent._stream_travel_response("杭州有哪些适合散步的地方", thread_id, False, []))
        history = agent.get_messages(thread_id)
        assistant = next(item for item in history if item["role"] == "assistant")
        stages = [item.get("stage") for item in assistant.get("activities", [])]
        titles = [item.get("title") for item in assistant.get("activities", [])]
        assert "progress" in stages
        assert "我先查询目的地的已验证地点。" in titles
    finally:
        delete_travel_thread(thread_id)


def test_more_transport_request_returns_next_structured_page(monkeypatch):
    current_plan = TravelPlan(
        plan_id="plan_more_transport",
        thread_id="guest_more_transport",
        version=1,
        timezone="Asia/Shanghai",
        start_date="2026-09-02",
        end_date="2026-09-02",
        origin="上海",
        destination="杭州",
        transport_pages=[
            TransportPage(mode="rail", offset=0, returned_count=5, total_count=8, has_more=True, filter="high_speed")
        ],
    )
    next_option = TransportOption(mode="rail", title="G105")
    next_page = TransportPage(mode="rail", offset=5, returned_count=1, total_count=8, has_more=False, filter="high_speed")
    captured: dict = {}

    monkeypatch.setattr(agent, "get_current_plan", lambda thread_id, user_id=None: current_plan)
    monkeypatch.setattr(
        agent,
        "get_transport_page",
        lambda plan, mode, *, offset, limit: captured.update(
            {"mode": mode, "offset": offset, "limit": limit}
        ) or TransportQueryPage(options=[next_option], pages=[next_page]),
    )

    chunks = list(agent.stream_chat("再给我 3 条高铁", "guest_more_transport", False, []))
    rendered = "".join(
        item.get("text", "")
        for chunk, _metadata in chunks
        if isinstance(chunk, AIMessageChunk)
        for item in (chunk.content if isinstance(chunk.content, list) else [])
        if isinstance(item, dict)
    )
    metadata = [item for _chunk, item in chunks if item.get("transport_page")][-1]

    assert captured == {"mode": "rail", "offset": 5, "limit": 3}
    assert "G105" in rendered
    assert metadata["transport_options"][0]["title"] == "G105"
    assert metadata["transport_page"]["has_more"] is False


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
