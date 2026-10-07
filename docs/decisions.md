# Decisions and scope record

## Frozen scope from PRD v1

- P0: FR-01 local text chat; FR-02 stable persona; FR-03 traceable confirmed memory; FR-04 persistent character and relationship state; FR-05 bounded life continuity; FR-06 page-only initiative; FR-07 user-confirmed persona evolution; FR-08 one real provider plus fake mode and diagnostics; FR-09 consistent backup and offline restore; FR-10 idempotent, single-writer, failure-safe turn submission.
- P1, deferred: background life progression, out-of-app notifications, vector/Mem0 retrieval, multi-provider specialization, streaming, automatic summaries, bulk history import, approved persona-theme expansion.
- P2, excluded: multi-user, IM channels, public mobile access, accounts, voice, image generation, social publishing, agent tools, internet actions, complex world simulation, desktop packaging.
- Fixed stack: Python 3.12.14; FastAPI/Uvicorn; Pydantic v2; stdlib sqlite3; vanilla HTML/CSS/JS; one process on 127.0.0.1:8000; one database as runtime source of truth.
- Storage/model boundary: SQLite owns runtime state; FakeClient is offline only; no Mem0/Qdrant, frontend build chain, Docker, Redis, background scheduler, or desktop package in v1.

## User decisions pending

- None identified during T00.

## Change log

- T00 recorded the PRD scope without changing fields, interfaces, requirements, or acceptance criteria.
- Conversation polish keeps the PRD v1 scope intact. The style layer responds directly to the user's message and avoids forced follow-up questions or invented daily details.
- Character fact clarification: 年糕 is 苏晚's own cat. This does not imply any interaction between 年糕 and the user; such interaction requires an explicit user-provided fact or a supported event.
- Shinsekai reference review: its README describes a dialogue-focused chat window separate from a React settings center for API, characters, templates, and extensions. Wanwan keeps the same separation principle: the main surface stays conversation-first and management/diagnostics remain secondary. RPG演出、TTS/ASR 和插件/MCP 能力不纳入当前范围。
- T12's bounded real-provider evaluation is complete. User-facing behavior review and desktop/narrow-screen feedback remain follow-up items; the existing deferred P1 list is unchanged.

