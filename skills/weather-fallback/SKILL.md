---
name: weather-fallback
description: Handle travel weather questions in AI_Agent using the TravelSupervisor and AMap weather first, with authorized web search only as a fallback. Use when Codex needs to improve weather lookups, location normalization, forecast formatting, or time-sensitive weather answer quality.
---

# Weather Fallback

## Workflow

1. Normalize the user location.
2. Let the TravelSupervisor call `get_weather` for live weather or forecast.
3. If the weather API is unavailable, let the TravelSupervisor use authorized web search.
4. In fallback mode, search with:
   - current date
   - city name
   - weather intent
5. If no trustworthy same-day result exists, say so clearly.

## Answer Rules

- Include location, update time, and source.
- Distinguish live weather from forecast.
- Avoid pretending a forecast page is a real-time observation.
