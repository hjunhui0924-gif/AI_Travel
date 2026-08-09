# Architecture Notes

## Core layers

`app.py`

- Hosts the FastAPI app
- Serves static frontend assets
- Accepts multipart chat submissions
- Streams model output back to the browser

`agents/agent.py`

- Initializes the language model
- Defines optional web search
- Persists conversation state with SQLite checkpoints
- Builds hidden attachment and search context sections

`utils/file_utils.py`

- Parses uploaded files into bounded text
- Centralizes supported extensions and extraction limits
- Provides the parsing primitive used by the chat endpoint

`static/*`

- Implements the chat UX
- Maintains local UI state for current session and search toggle

## Change guidance

- Keep backend parsing and frontend accepted file types aligned
- Prefer shared helpers over duplicated search or parsing logic
- Preserve streaming responses; do not regress to a blocking full-response flow unless explicitly needed
