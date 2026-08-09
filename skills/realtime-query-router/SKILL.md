---
name: realtime-query-router
description: Route real-time or date-sensitive travel questions in AI_Agent to the most reliable tool path. Use when Codex needs to improve handling for queries involving today, latest, current, real-time, weather, traffic, transport schedules, POI availability, or any other information that can go stale quickly.
---

# Realtime Query Router

## Overview

Use this skill when extending the project for time-sensitive questions.

Prefer this routing order:

1. Dedicated API or structured tool
2. Authorized domain adapter
3. Web search fallback with date checks
4. Clear uncertainty warning if freshness cannot be verified

## Routing Rules

- Weather questions:
  Use `weather_lookup` first.
- “Today / latest / current / real-time” questions:
  Always inject current date context before tool selection.
- Generic current-events questions:
  Use `web_search`, but include date validation and source cards.

## Output Rules

- Never present stale results as current facts.
- If sources show older dates, explicitly say so.
- Prefer concise answers followed by sources, not the other way around.
