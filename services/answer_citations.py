"""Parse and validate sentence-level citations in assistant answers.

The model is allowed to emit an internal marker such as
``[[cite:web_abc123]]`` after a sentence that is directly supported by a
web-search result.  The marker is never part of the public answer.  This
module turns it into a stable, frontend-friendly list of answer segments and
rejects IDs that are not present in the response's source list.

Keeping this logic outside the HTTP and model orchestration layers makes the
same contract apply to ordinary chat, travel planning, replanning, and
history restoration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


SOURCE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
# The marker is an internal protocol, so malformed attempts must be removed
# too.  A deliberately broad, non-greedy token prevents a stray closing bracket
# inside an invalid ID from leaking the rest of the marker into the UI.
_CITATION_TOKEN_PATTERN = re.compile(r"\[\[cite:(?P<source_id>[\s\S]*?)\]\]")
_INCOMPLETE_CITATION_PATTERN = re.compile(r"\[\[cite:(?:(?!\]\])[\s\S])*$")
_INTERNAL_ID_LABEL_PATTERN = re.compile(r"\bCitation ID:\s*[A-Za-z0-9_-]+\b", re.IGNORECASE)
_SENTENCE_BOUNDARIES = frozenset("。！？!?.\n")
_CLOSING_DELIMITERS = frozenset("\"'“”‘’「」『』（）()[]【】《》<>〉》")
_INCOMPLETE_PRESERVE_BOUNDARIES = frozenset(" \t\u3000.,，、。！？!?；;:：)]}》」』”’\"'")


@dataclass(frozen=True, slots=True)
class CitationParseResult:
    """The public answer plus the citation relationships extracted from it."""

    final_text: str
    answer_segments: list[dict[str, Any]]
    used_source_ids: list[str]
    invalid_source_ids: list[str]


def citation_marker(source_id: str) -> str:
    """Return the internal marker used by deterministic renderers."""

    value = str(source_id or "").strip()
    if not SOURCE_ID_PATTERN.fullmatch(value):
        return ""
    return f"[[cite:{value}]]"


def _is_http_url(value: object) -> bool:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _remove_incomplete_marker(value: str) -> tuple[str, str]:
    """Remove an unfinished marker while retaining an obvious following answer."""

    match = _INCOMPLETE_CITATION_PATTERN.search(value)
    if not match:
        return value, ""
    prefix = value[:match.start()]
    body = value[match.start() + len("[[cite:") :]
    id_match = re.match(r"[A-Za-z0-9_-]*", body)
    raw_id = (id_match.group(0) if id_match else "").strip()
    boundary = body[id_match.end()] if id_match and id_match.end() < len(body) else ""
    # A space/tab or sentence punctuation unambiguously ends a malformed ID.
    # Newlines remain conservative: they can be a streaming split inside the
    # marker and are therefore held back instead of being emitted.
    if boundary in _INCOMPLETE_PRESERVE_BOUNDARIES:
        return prefix + body[id_match.end() :], raw_id
    return prefix, raw_id or body.strip()


def _allowed_web_sources(sources: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    allowed: dict[str, Mapping[str, Any]] = {}
    seen: dict[str, Mapping[str, Any]] = {}
    conflicts: set[str] = set()
    for source in sources or []:
        if not isinstance(source, Mapping):
            continue
        source_id = str(source.get("evidence_id") or "").strip()
        if not SOURCE_ID_PATTERN.fullmatch(source_id):
            continue
        existing = seen.get(source_id)
        if existing is not None:
            for field in ("url", "source_type", "provider"):
                old_value = str(existing.get(field) or "").strip()
                new_value = str(source.get(field) or "").strip()
                if old_value and new_value and old_value != new_value:
                    conflicts.add(source_id)
        seen.setdefault(source_id, source)
        # Sentence-level citations are intentionally limited to public web
        # search evidence.  Map, weather, rail, and flight adapters have
        # different freshness/UX semantics and must not be conflated with it.
        if str(source.get("source_type") or "").strip() != "web_search":
            continue
        if not _is_http_url(source.get("url")):
            continue
        allowed.setdefault(source_id, source)
    return {source_id: source for source_id, source in allowed.items() if source_id not in conflicts}


def _sentence_start(text: str, end: int) -> int:
    """Find the beginning of the sentence ending immediately before *end*."""

    if end <= 0:
        return 0
    index = end - 1
    # A marker normally follows punctuation.  Skip that punctuation first so
    # a citation after ``A。B。`` belongs to B rather than A+B.
    while index >= 0 and text[index] in _CLOSING_DELIMITERS:
        index -= 1
    while index >= 0 and text[index] in _SENTENCE_BOUNDARIES:
        index -= 1
    while index >= 0 and text[index] not in _SENTENCE_BOUNDARIES:
        index -= 1
    return index + 1


def _merge_spans(
    spans: list[tuple[int, int, list[str]]],
) -> list[tuple[int, int, list[str]]]:
    merged: list[tuple[int, int, list[str]]] = []
    for start, end, source_ids in sorted(spans, key=lambda item: (item[0], item[1])):
        if end <= start:
            continue
        if not merged or start >= merged[-1][1]:
            merged.append((start, end, list(dict.fromkeys(source_ids))))
            continue
        old_start, old_end, old_ids = merged[-1]
        merged[-1] = (
            old_start,
            max(old_end, end),
            list(dict.fromkeys([*old_ids, *source_ids])),
        )
    return merged


def _segments_from_spans(
    text: str,
    spans: list[tuple[int, int, list[str]]],
) -> list[dict[str, Any]]:
    if not text:
        return []
    if not spans:
        return [{"text": text, "source_ids": []}]

    segments: list[dict[str, Any]] = []
    cursor = 0
    for start, end, source_ids in spans:
        if start > cursor:
            segments.append({"text": text[cursor:start], "source_ids": []})
        if end > cursor:
            segments.append({"text": text[max(cursor, start):end], "source_ids": source_ids})
            cursor = end
    if cursor < len(text):
        segments.append({"text": text[cursor:], "source_ids": []})
    return [segment for segment in segments if segment["text"]]


def parse_answer_citations(
    text: str,
    sources: Sequence[Mapping[str, Any]] | None,
    *,
    citations_enabled: bool = True,
) -> CitationParseResult:
    """Remove internal markers and attach only verified web sources.

    Invalid or unknown citation IDs are removed from the rendered text but do
    not create a citation relationship.  This is important because model
    output is untrusted input: a model must not be able to invent a clickable
    URL or cite a non-web adapter merely by guessing an ID.
    """

    raw_text = _INTERNAL_ID_LABEL_PATTERN.sub("", str(text or ""))
    allowed = _allowed_web_sources(sources) if citations_enabled else {}
    parts: list[str] = []
    markers: list[tuple[int, str]] = []
    invalid_source_ids: list[str] = []
    raw_cursor = 0
    clean_position = 0

    for match in _CITATION_TOKEN_PATTERN.finditer(raw_text):
        prefix = raw_text[raw_cursor:match.start()]
        parts.append(prefix)
        clean_position += len(prefix)

        raw_source_id = match.group("source_id").strip()
        if SOURCE_ID_PATTERN.fullmatch(raw_source_id) and raw_source_id in allowed:
            markers.append((clean_position, raw_source_id))
        elif raw_source_id and raw_source_id not in invalid_source_ids:
            invalid_source_ids.append(raw_source_id)
        raw_cursor = match.end()

    tail = raw_text[raw_cursor:]
    # Do not let an incomplete internal marker leak into the public answer if
    # the model stopped streaming halfway through one.
    tail, raw_id = _remove_incomplete_marker(tail)
    if raw_id and raw_id not in invalid_source_ids:
        invalid_source_ids.append(raw_id)
    parts.append(tail)
    clean_text = "".join(parts)

    spans: list[tuple[int, int, list[str]]] = []
    for position, source_id in markers:
        end = len(clean_text[:position].rstrip())
        if end <= 0:
            continue
        spans.append((
            _sentence_start(clean_text, end),
            end,
            [source_id],
        ))

    merged_spans = _merge_spans(spans)
    answer_segments = _segments_from_spans(clean_text, merged_spans)
    used_source_ids = list(
        dict.fromkeys(
            source_id
            for _start, _end, source_ids in merged_spans
            for source_id in source_ids
        )
    )
    return CitationParseResult(
        final_text=clean_text,
        answer_segments=answer_segments,
        used_source_ids=used_source_ids,
        invalid_source_ids=invalid_source_ids,
    )


def validate_answer_segments(
    segments: object,
    final_text: str,
    sources: Sequence[Mapping[str, Any]] | None,
    *,
    citations_enabled: bool = True,
) -> list[dict[str, Any]]:
    """Validate structured segments produced by a trusted deterministic path."""

    text = str(final_text or "")
    if not isinstance(segments, list):
        return [{"text": text, "source_ids": []}] if text else []

    allowed_ids = set(_allowed_web_sources(sources)) if citations_enabled else set()
    normalized: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            return [{"text": text, "source_ids": []}] if text else []
        segment_text = str(segment.get("text") or "")
        if not segment_text:
            continue
        source_ids = []
        for source_id in segment.get("source_ids") or []:
            value = str(source_id or "").strip()
            if value in allowed_ids and value not in source_ids:
                source_ids.append(value)
        normalized.append({"text": segment_text, "source_ids": source_ids})

    if "".join(segment["text"] for segment in normalized) != text:
        return [{"text": text, "source_ids": []}] if text else []
    return normalized


def strip_citation_markers(text: str) -> str:
    """Remove complete and incomplete internal markers for history fallback."""

    value = _INTERNAL_ID_LABEL_PATTERN.sub("", str(text or ""))
    value = _CITATION_TOKEN_PATTERN.sub("", value)
    return _remove_incomplete_marker(value)[0]


def strip_citation_markers_for_stream(text: str) -> str:
    """Return the safe prefix that can be emitted while a response streams."""

    value = _INTERNAL_ID_LABEL_PATTERN.sub("", str(text or ""))
    value = _remove_incomplete_marker(value)[0]
    return _CITATION_TOKEN_PATTERN.sub("", value)


def _sanitize_source_record(source: Mapping[str, Any]) -> dict[str, Any]:
    """Remove internal protocol text and unsafe links from public source cards."""

    item: dict[str, Any] = {}
    for field, value in source.items():
        if field == "evidence_id":
            item[field] = str(value or "").strip()
        elif isinstance(value, str):
            item[field] = strip_citation_markers(value)
        elif isinstance(value, list):
            item[field] = [
                strip_citation_markers(entry) if isinstance(entry, str) else entry
                for entry in value
            ]
        else:
            item[field] = value

    if item.get("evidence_id") and not SOURCE_ID_PATTERN.fullmatch(item["evidence_id"]):
        item["evidence_id"] = ""
    if isinstance(item.get("url"), str):
        url = item["url"].strip()
        item["url"] = url if not url or _is_http_url(url) else ""
    return item


def deduplicate_sources(sources: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Deduplicate source cards while retaining fields from later records.

    If two records claim the same evidence ID but disagree on identity fields,
    discard that ID entirely.  A clickable citation must never resolve
    nondeterministically to one of two URLs.
    """

    records: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    conflicts: set[str] = set()
    for source in sources or []:
        if not isinstance(source, Mapping):
            continue
        item = _sanitize_source_record(source)
        key = str(item.get("evidence_id") or "").strip()
        if not key:
            url = str(item.get("url") or "").strip()
            key = f"url:{url}" if url else f"title:{item.get('title', '')}"
        if key in records:
            existing = records[key]
            for field in ("url", "source_type", "provider"):
                old_value = str(existing.get(field) or "").strip()
                new_value = str(item.get(field) or "").strip()
                if old_value and new_value and old_value != new_value:
                    conflicts.add(key)
            for field, value in item.items():
                if field == "supports" and isinstance(value, list) and value:
                    old_supports = existing.get(field) if isinstance(existing.get(field), list) else []
                    existing[field] = list(dict.fromkeys([*old_supports, *value]))
                elif not existing.get(field) and value:
                    existing[field] = value
            continue
        records[key] = item
        order.append(key)
    return [records[key] for key in order if key not in conflicts]
