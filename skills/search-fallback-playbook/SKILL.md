---
name: search-fallback-playbook
description: Handle travel cases in AI_Agent where no domain-specific API is available and the TravelSupervisor must fall back to authorized web search with current-date context, location extraction, result filtering, and staleness warnings. Use when Codex needs to improve travel search fallback quality.
---

# Search Fallback Playbook

## Goal

Use this skill when a question is time-sensitive but no dedicated API is available.

## Workflow

1. Identify whether the question depends on the current date.
2. Extract the main entity:
   - city
   - product
   - person
   - policy topic
   - event
3. Add current date context before searching.
4. Run multiple search variants:
   - original query
   - query + current date
   - query + current year
   - query + today/current keywords
5. Compare returned results.
6. Prefer sources that:
   - show explicit dates
   - are close to today
   - come from higher-trust domains
7. If freshness cannot be confirmed, warn explicitly.

## Output Rules

- Never present stale results as current facts.
- Surface date clues and source links.
- Keep fallback separate from dedicated API results when both exist.
