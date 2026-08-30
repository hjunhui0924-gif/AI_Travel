from __future__ import annotations

import re


PLACE_LABELS = [
    "\u9152\u5e97",
    "\u5165\u4f4f",
    "\u4f1a\u573a",
    "\u4f1a\u8bae",
    "\u666f\u70b9",
    "\u9910\u5385",
    "\u5496\u5561\u9986",
]

TRAILING_ACTION_WORDS = [
    "\u5f00\u4f1a",
    "\u53c2\u4f1a",
    "\u5165\u4f4f",
    "\u4f4f\u5bbf",
    "\u5403\u996d",
    "\u7528\u9910",
    "\u6e38\u73a9",
    "\u6253\u5361",
    "\u53bb\u73a9",
]

PLACE_STOP_WORDS = [
    "\u600e\u4e48\u9009",
    "\u600e\u4e48\u53bb",
    "\u600e\u4e48\u8d70",
    "\u9644\u8fd1",
    "\u5468\u8fb9",
    "\u63a8\u8350",
    "\u653b\u7565",
    "\u9910\u5385",
    "\u5496\u5561\u9986",
    "\u666f\u70b9",
    "\u9152\u5e97",
    "\u9ad8\u94c1",
    "\u822a\u73ed",
]

GENERIC_PLACE_PHRASES = {
    "美食",
    "本地美食",
    "当地美食",
    "好吃的",
    "餐厅",
    "附近餐厅",
    "景点",
    "附近景点",
    "周边景点",
    "慢节奏",
    "轻松",
    "不赶路",
    "少走路",
    "多安排美食",
    "节奏慢一些",
    "节奏慢一点",
    "慢一点",
    "慢慢玩",
    "不赶景点",
}

LEADING_CONTEXT_WORDS = [
    "\u6211\u4f4f\u5728",
    "\u6211\u5165\u4f4f\u5728",
    "\u4f4f\u5728",
    "\u5165\u4f4f",
    "\u4f1a\u573a\u5728",
    "\u5728",
    "包含",
    "包括",
    "途经",
    "打算去",
    "计划去",
    "想去",
    "游览",
    "游玩",
]


def _clean_value(value: str) -> str:
    cleaned = (
        str(value or "")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip(" \t:：,，。.;；")
        .strip()
    )
    for token in TRAILING_ACTION_WORDS:
        if cleaned.endswith(token) and len(cleaned) > len(token) + 1:
            cleaned = cleaned[: -len(token)].strip(" \t:：,，。.;；")
    for token in LEADING_CONTEXT_WORDS:
        if cleaned.startswith(token) and len(cleaned) > len(token) + 1:
            cleaned = cleaned[len(token):].strip(" \t:：,，。.;；")
    return cleaned


def _append_place(places: list[str], value: str) -> None:
    cleaned = _clean_value(value)
    if not cleaned or cleaned in places or cleaned in GENERIC_PLACE_PHRASES:
        return
    if any(stop_word in cleaned for stop_word in PLACE_STOP_WORDS):
        if not any(label in cleaned for label in ["宾馆", "酒店", "景区", "公园", "博物馆", "中心", "大厦", "广场", "西湖"]):
            return
    places.append(cleaned)


def extract_attachment_notes(attachments: list[dict]) -> list[str]:
    notes: list[str] = []
    for attachment in attachments:
        name = attachment.get("name", "")
        if name:
            notes.append(f"已收到附件: {name}")

        content = attachment.get("content", "")
        if not content:
            continue

        for pattern in [
            r"(20\d{2}-\d{1,2}-\d{1,2})",
            r"([A-Z]{2}\d{3,4})",
            r"(\u9152\u5e97|\u5165\u4f4f|\u9000\u623f|\u4f1a\u8bae|\u4f1a\u573a)",
        ]:
            match = re.search(pattern, content)
            if match:
                notes.append(f"附件线索: {match.group(1)}")
    return notes[:6]


def extract_named_places(attachments: list[dict], message: str = "") -> list[str]:
    places: list[str] = []

    attachment_patterns = [
        r"(?:\u5165\u4f4f|\u9152\u5e97)\s*[:：]?\s*([^\n,，。.;；]{2,40})",
        r"(?:\u4f1a\u573a|\u4f1a\u8bae)\s*[:：]?\s*([^\n,，。.;；]{2,40})",
        r"(?:\u666f\u70b9)\s*[:：]?\s*([^\n,，。.;；]{2,40})",
        r"(?:\u9910\u5385|\u5496\u5561\u9986)\s*[:：]?\s*([^\n,，。.;；]{2,40})",
    ]

    for attachment in attachments:
        content = attachment.get("content", "")
        if not content:
            continue
        for pattern in attachment_patterns:
            for match in re.findall(pattern, content):
                _append_place(places, match)

    message_patterns = [
        r"(?:\u4f4f\u5728|\u5165\u4f4f)([^\n,，。.;；]{2,30})",
        r"(?:\u53bb|到)([^\n,，。.;；]{2,24}?)(?:\u5f00\u4f1a|\u53c2\u4f1a|\u73a9|\u5403|\u5165\u4f4f|$)",
        r"(?:\u5728)([^\n,，。.;；]{2,24}?)(?:\u5f00\u4f1a|\u53c2\u4f1a|$)",
        r"(?:顺便去|顺路去|想去)([^\n,，。.;；]{2,24})",
    ]

    # Explicit lists are the most reliable route anchors. Split them before
    # the broader sentence patterns so "包含西湖、灵隐寺和河坊街" becomes
    # three city-scoped place searches instead of one ambiguous long string.
    explicit_list_patterns = [
        r"(?:包含|包括|途经|打算去|计划去|想去|游览|游玩)([^\n。！？!?]{2,120})",
        r"(?:顺便去|顺路去)([^\n。！？!?]{2,120})",
    ]
    for pattern in explicit_list_patterns:
        for match in re.findall(pattern, message):
            candidates = re.split(r"[、,，;；]|以及|还有|和|及|与", match)
            for candidate in candidates:
                _append_place(places, candidate)

    for pattern in message_patterns:
        for match in re.findall(pattern, message):
            cleaned = _clean_value(match)
            if cleaned and (
                any(label in cleaned for label in PLACE_LABELS)
                or len(cleaned) >= 4
                or any(ch in cleaned for ch in ["\u5bbe\u9986", "\u9152\u5e97", "\u4e2d\u5fc3", "\u5927\u53a6", "\u897f\u6e56"])
            ):
                _append_place(places, cleaned)

    inline_place_patterns = [
        r"([^\s,，。.;；]{2,20}(?:酒店|宾馆|民宿|会展中心|会议中心|大厦|广场|景区|公园|博物馆|西湖))",
    ]
    for pattern in inline_place_patterns:
        for match in re.findall(pattern, message):
            _append_place(places, match)

    return places[:6]
