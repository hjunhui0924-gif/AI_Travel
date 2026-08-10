import json
from dataclasses import asdict
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain.messages import AIMessage, AIMessageChunk

import app as app_module
from agents import agent as agent_module
from agents.schemas import Evidence, PoiRecommendation, TravelPlanResponse
from agents.travel_agent import render_travel_response
from services.answer_citations import (
    deduplicate_sources,
    parse_answer_citations,
    strip_citation_markers_for_stream,
)
from services.travel_store import delete_travel_thread, ensure_guest_access


def _web_source(source_id="web_abc123"):
    return {
        "evidence_id": source_id,
        "source_type": "web_search",
        "provider": "Tavily",
        "title": "公开页面",
        "url": "https://example.com/article",
    }


def test_citations_are_sentence_scoped_and_unknown_ids_are_removed():
    result = parse_answer_citations(
        "第一句。[[cite:web_abc123]] 第二句没有来源。[[cite:forged]]",
        [_web_source()],
    )

    assert result.final_text == "第一句。 第二句没有来源。"
    assert result.answer_segments == [
        {"text": "第一句。", "source_ids": ["web_abc123"]},
        {"text": " 第二句没有来源。", "source_ids": []},
    ]
    assert result.invalid_source_ids == ["forged"]
    assert "[[cite:" not in result.final_text

    internal_label = parse_answer_citations(
        "依据。 Citation ID: web_abc123",
        [_web_source()],
    )
    assert internal_label.final_text == "依据。 "

    adjacent = parse_answer_citations(
        "第一句。[[cite:web_abc123]]第二句。[[cite:web_def456]]",
        [_web_source(), _web_source("web_def456")],
    )
    assert adjacent.answer_segments == [
        {"text": "第一句。", "source_ids": ["web_abc123"]},
        {"text": "第二句。", "source_ids": ["web_def456"]},
    ]


def test_non_web_sources_cannot_be_used_as_clickable_citations():
    result = parse_answer_citations(
        "航班已查询。[[cite:flight_001]]",
        [
            {
                "evidence_id": "flight_001",
                "source_type": "flight_realtime",
                "provider": "VariFlight",
                "url": "https://example.com/flight",
            }
        ],
    )

    assert result.final_text == "航班已查询。"
    assert result.answer_segments == [{"text": "航班已查询。", "source_ids": []}]
    assert result.invalid_source_ids == ["flight_001"]


def test_disabled_search_cannot_activate_web_citations_even_with_a_source_record():
    result = parse_answer_citations(
        "这句来自网页。[[cite:web_abc123]]",
        [_web_source()],
        citations_enabled=False,
    )

    assert result.final_text == "这句来自网页。"
    assert result.answer_segments == [{"text": "这句来自网页。", "source_ids": []}]


def test_citation_parser_handles_continuous_punctuation_and_malformed_tokens():
    result = parse_answer_citations(
        "Really?![[cite:web_abc123]] 下一句。[[cite:web_abc123\nmalformed]]",
        [_web_source()],
    )

    assert result.final_text == "Really?! 下一句。"
    assert result.answer_segments == [
        {"text": "Really?!", "source_ids": ["web_abc123"]},
        {"text": " 下一句。", "source_ids": []},
    ]
    assert "[[cite:" not in result.final_text

    malformed = parse_answer_citations(
        "保留文本[[cite:web_abc123]非法括号]]之后",
        [_web_source()],
    )
    assert malformed.final_text == "保留文本之后"

    unfinished = parse_answer_citations(
        "前文[[cite:web_abc123 后文仍然是答案",
        [_web_source()],
    )
    assert unfinished.final_text == "前文 后文仍然是答案"


def test_citation_sentence_includes_closing_quote_and_not_semicolon_clause_only():
    quoted = parse_answer_citations(
        '他说“已经完成。”[[cite:web_abc123]] 下一句。',
        [_web_source()],
    )
    assert quoted.answer_segments[0] == {
        "text": '他说“已经完成。”',
        "source_ids": ["web_abc123"],
    }

    semicolon = parse_answer_citations(
        "A；B。[[cite:web_abc123]]",
        [_web_source()],
    )
    assert semicolon.answer_segments == [
        {"text": "A；B。", "source_ids": ["web_abc123"]},
    ]


def test_conflicting_source_identity_cannot_be_cited():
    result = parse_answer_citations(
        "网页支持这句话。[[cite:web_abc123]]",
        [
            _web_source(),
            {
                **_web_source(),
                "url": "https://another.example/article",
            },
        ],
    )

    assert result.answer_segments == [
        {"text": "网页支持这句话。", "source_ids": []},
    ]
    assert result.used_source_ids == []


def test_web_search_permission_guard_blocks_direct_tool_use(monkeypatch):
    class ExplodingSearcher:
        def invoke(self, payload):
            raise AssertionError("searcher must not be called")

    monkeypatch.setattr(agent_module, "_raw_web_search", ExplodingSearcher())
    agent_module._reset_runtime_buffers()
    agent_module._web_search_allowed_var.set(False)
    try:
        result = agent_module.perform_web_search("最新旅行信息")
    finally:
        agent_module._reset_runtime_buffers()

    assert "未开启联网搜索" in result


def test_stream_sanitizer_holds_incomplete_marker_without_leaking_it():
    assert strip_citation_markers_for_stream("依据如下[[cite:web_") == "依据如下"
    assert strip_citation_markers_for_stream("依据如下[[cite:web_\n下一行") == "依据如下"
    assert strip_citation_markers_for_stream("依据[[cite:web_abc123]]。") == "依据。"
    assert strip_citation_markers_for_stream("依据[[cite:]]。") == "依据。"


def test_source_deduplication_merges_complete_later_metadata():
    sources = deduplicate_sources(
        [
            {"evidence_id": "web_abc123", "title": "页面"},
            {
                "evidence_id": "web_abc123",
                "source_type": "web_search",
                "url": "https://example.com/article",
                "supports": ["web_search_result"],
            },
        ]
    )

    assert sources == [
        {
            "evidence_id": "web_abc123",
            "title": "页面",
            "source_type": "web_search",
            "url": "https://example.com/article",
            "supports": ["web_search_result"],
        }
    ]


def test_source_cards_remove_internal_markers_and_unsafe_urls():
    sources = deduplicate_sources(
        [
            {
                "evidence_id": "web_unsafe",
                "source_type": "web_search",
                "provider": "Tavily",
                "title": "标题 [[cite:web_unsafe]]",
                "summary": "摘要 Citation ID: web_unsafe",
                "url": "javascript:alert(1)",
            },
            {
                "evidence_id": "web_bad_url",
                "source_type": "web_search",
                "url": "http://[",
            },
        ]
    )

    assert sources[0]["title"] == "标题 "
    assert sources[0]["summary"] == "摘要 "
    assert sources[0]["url"] == ""
    assert sources[1]["url"] == ""


def test_history_keeps_answer_whitespace_and_respects_disabled_search():
    source = _web_source()
    answer_segments = [{"text": "  网页结论  ", "source_ids": ["web_abc123"]}]
    content = "  网页结论  " + agent_module.encode_assistant_metadata(
        [],
        [source],
        answer_segments,
        search_enabled=False,
    )

    history = agent_module._stored_turn_messages(
        [
            {
                "role": "assistant",
                "content": content,
                "search_enabled": False,
                "attachments": [],
                "plan_id": None,
                "plan_version": None,
            }
        ]
    )

    assert history[0]["content"] == "  网页结论  "
    assert history[0]["answer_segments"] == [
        {"text": "  网页结论  ", "source_ids": []},
    ]

    legacy_content = "旧格式" + (
        f"\n\n{agent_module.ASSISTANT_META_START}"
        + json.dumps(
            {
                "sources": [source],
                "answer_segments": [{"text": "旧格式", "source_ids": ["web_abc123"]}],
                "search_enabled": "false",
            },
            ensure_ascii=False,
        )
        + agent_module.ASSISTANT_META_END
    )
    legacy_history = agent_module._stored_turn_messages(
        [{"role": "assistant", "content": legacy_content, "search_enabled": False}]
    )
    assert legacy_history[0]["answer_segments"] == [
        {"text": "旧格式", "source_ids": []},
    ]


def test_checkpoint_metadata_sanitizes_all_text_blocks(monkeypatch):
    source = _web_source()
    message = AIMessage(
        content=[
            {"type": "text", "text": "第一段[[cite:web_abc123]]"},
            {"type": "text", "text": "第二段"},
            {"type": "image_url", "image_url": {"url": "https://example.com/image"}},
        ]
    )

    class FakeCheckpoint:
        def __init__(self):
            self.payload = {
                "channel_values": {"messages": [message]},
                "checkpoint": {},
                "metadata": {},
                "new_versions": {},
            }
            self.put_called = False

        def get(self, _config):
            return self.payload

        def put(self, _config, *_args):
            self.put_called = True

    fake_checkpoint = FakeCheckpoint()
    monkeypatch.setattr(agent_module, "checkpoint", fake_checkpoint)

    agent_module.attach_assistant_metadata(
        "guest_checkpoint_test",
        "第一段[[cite:web_abc123]]第二段",
        [],
        [source],
        [{"text": "第一段第二段", "source_ids": []}],
        search_enabled=True,
    )

    assert fake_checkpoint.put_called is True
    assert message.content[0]["text"].startswith("第一段第二段")
    assert "[[cite:" not in message.content[0]["text"]
    assert message.content[1]["text"] == ""
    assert message.content[2]["image_url"] == {"url": "https://example.com/image"}


def test_travel_render_cites_only_web_popularity_signal():
    source = Evidence(
        evidence_id="web_abc123",
        source_type="web_search",
        provider="Tavily",
        title="热门地点介绍",
        url="https://example.com/article",
    )
    response = TravelPlanResponse(
        intent="nearby_explore",
        summary="附近探索",
        poi_recommendations=[
            PoiRecommendation(
                name="湖边餐厅",
                category="美食",
                rating="4.8",
                popularity_signal="网页结果提及（不等同于评分）",
                source_ids=["map_poi_1", "web_abc123"],
            )
        ],
        sources=[source],
    )

    rendered = render_travel_response(response)
    assert "[[cite:web_abc123]]" in rendered
    parsed = parse_answer_citations(rendered, [asdict(source)])
    assert "[[cite:" not in parsed.final_text
    assert "web_abc123" in parsed.used_source_ids


def test_chat_done_and_history_expose_structured_citations(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)

    def fake_stream_chat(**kwargs):
        agent_module._reset_runtime_buffers()
        agent_module._log_source_card(
            "公开页面",
            "https://example.com/article",
            "公开摘要",
            evidence_id="web_abc123",
            source_type="web_search",
            provider="Tavily",
        )
        yield AIMessageChunk(
            content=[
                {
                    "type": "text",
                    "text": "第一句。[[cite:web_abc123]] 第二句。",
                }
            ]
        ), {}

    monkeypatch.setattr(app_module, "stream_chat", fake_stream_chat)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)
    try:
        response = client.post(
            "/chat",
            data={
                "message": "查一下",
                "thread_id": thread_id,
                "search_enabled": "true",
            },
        )
        assert response.status_code == 200
        payloads = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        done = payloads[-1]
        assert done["final_text"] == "第一句。 第二句。"
        assert done["answer_segments"] == [
            {"text": "第一句。", "source_ids": ["web_abc123"]},
            {"text": " 第二句。", "source_ids": []},
        ]
        assert "[[cite:" not in response.text
        assert done["sources"][0]["evidence_id"] == "web_abc123"

        history = client.get(f"/history/{thread_id}")
        assert history.status_code == 200
        assistant = history.json()["messages"][-1]
        assert assistant["content"] == "第一句。 第二句。"
        assert assistant["answer_segments"] == done["answer_segments"]
    finally:
        delete_travel_thread(thread_id)


def test_source_sse_is_sanitized_before_frontend_receives_it(monkeypatch):
    thread_id = f"guest_{uuid4().hex}"
    guest_token = ensure_guest_access(thread_id)

    def fake_stream_chat(**kwargs):
        agent_module._reset_runtime_buffers()
        agent_module._log_source_card(
            "标题 [[cite:web_unsafe]]",
            "javascript:alert(1)",
            "摘要 Citation ID: web_unsafe",
            evidence_id="web_unsafe",
            source_type="web_search",
            provider="Tavily",
        )
        yield AIMessageChunk(content=[{"type": "text", "text": "普通答案。"}]), {}

    monkeypatch.setattr(app_module, "stream_chat", fake_stream_chat)
    client = TestClient(app_module.app)
    client.cookies.set(app_module.GUEST_COOKIE_NAME, guest_token)
    try:
        response = client.post(
            "/chat",
            data={
                "message": "查询",
                "thread_id": thread_id,
                "search_enabled": "true",
            },
        )
        assert response.status_code == 200
        events = []
        event_type = ""
        for line in response.text.splitlines():
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                events.append((event_type, json.loads(line[6:])))
        source_events = [payload for kind, payload in events if kind == "source"]
        assert source_events == [
            {
                "title": "标题 ",
                "url": "",
                "summary": "摘要 ",
                "source_date": "",
                "evidence_id": "web_unsafe",
                "source_type": "web_search",
                "provider": "Tavily",
            }
        ]
        assert "[[cite:" not in response.text
    finally:
        delete_travel_thread(thread_id)
