---
name: entity-normalizer
description: Normalize user-provided places, transit codes, and shorthand references in AI_Agent before calling travel APIs or web search. Use when Codex needs to improve recognition of city, district, airport, station, attraction, or other ambiguous travel entities.
---

# Entity Normalizer

## Targets

- City names and districts
- Airport and railway-station names/codes
- Attraction, hotel, and restaurant aliases

## Workflow

1. Detect whether the entity is already canonical.
2. Expand shorthand to a canonical form.
3. Preserve the user-facing form for display.
4. Use the canonical form for tools and APIs.

## Examples

- “浦东” -> normalized city/district context before weather or map queries
- “虹桥” -> resolve to the relevant airport/station context before a transport query
