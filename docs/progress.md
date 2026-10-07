# Progress

> Snapshot: 2026-10-07 (Asia/Tokyo). T00–T11 are complete. T12 real-provider technical evaluation is complete under separate hard-capped batches; user subjective behavior sign-off remains pending.

## Project baseline

- Project directory: E:\agents\wanwan
- Remote: https://github.com/zgj0707/wanwan-chatbot.git (private repository, default branch main).
- Verified baseline: main at 43e9fc778bbfcbeaad6dc68264af22ff682cb720; the remote tree contained only its 16-byte README.md.
- Python selected for this project: 3.12.14; SQLite runtime: 3.53.1.
- Runtime source: Codex bundled Python 3.12.14; system python and py launchers were not on PATH. T01 created the project `.venv` from this interpreter.
- Git baseline: the task directory initially contained only outputs/ and work/, with no .git. The private GitHub repository was read using the connected GitHub integration. Local Git HTTPS access failed at credential/TLS setup, so the verified main commit was reconstructed locally from the GitHub commit/tree/blob objects; the computed Git commit SHA matches the remote.

## Current implementation after T11

- Chat UI now prioritizes the conversation and session list, moves management pages below the history, adds a mobile sidebar drawer, and offers optional starter prompts that fill the composer without sending.
- Runtime chat prompting has a separate conversational style guide with concise Chinese examples. Simple greetings should receive a natural greeting and at most one non-assumptive follow-up.
- Persona clarification: 年糕 is 苏晚's British Shorthair cat, not the user's cat. The prompt must not imply that the user lives with, feeds, cares for, or is woken by 年糕 unless the user has explicitly described that interaction.
- Initial local real-mode smoke: one isolated temporary-database chat completed with two provider calls (analysis and chat); full T12 evidence is recorded below.
- Chat polish now has concise live-chat guidance, boundary checks for explicit guard questions, unsupported coworker events, and cat-ownership corrections. Obvious parenthetical action asides are cleaned from saved chat text; a simple greeting that over-asks is normalized to one conversational question.
- Final T12 19-turn batch completed all turns, retrieved all 10 expected memory facts, injected the expected committed event, and had no source-reference leaks. See [acceptance.md](acceptance.md) for batch budgets, latency, tokens, and remaining behavior gaps.
- The project folder has been moved to `E:\agents\wanwan`; ignored `.env`, `config.yaml`, `.venv`, and `data/` remain local and are not part of the PR.

## Near-term roadmap

1. T12 technical evaluation is complete. Record actual model cost only if a rate card becomes available; do not treat unknown as zero.
2. Review remaining user-facing behavior in `tests/fixtures/behavior_cases.json`, especially direct answers from confirmed memory, explicit virtual-role disclosure, and the user's preferred conversational tone.
3. Continue desktop and narrow-screen review. Following the Shinsekai README's dialogue-window/settings-center separation, keep the chat surface focused on conversation and management controls secondary; keep deferred P1 work out of scope until user feedback.

## Source copies and SHA-256

| Source file | Bytes | SHA-256 | Project copy |
|---|---:|---|---|
| 苏晚Chatbot-可执行PRD_v1.md | 74870 | 8807C0834515A3DD04E2343519B10BF18132FF080932F4044D69A1047B3E2593 | docs/source/苏晚Chatbot-可执行PRD_v1.md |
| 苏晚-完整人设_当前版本.md | 7655 | 53DD622182874DACE089A96C7B833E06F5811D04766A50F81CEC40923D3B345A | docs/source/苏晚-完整人设_当前版本.md |
| 苏晚-对话角色包.md | 13837 | AA0C2E097ACFD569157B0A32283F7C986936C2B787E3478297A3E1AEBA62D3B5 | docs/source/苏晚-对话角色包.md |

## T00 — scope freeze and startup assets

- Status: PASS.
- Scope: P0 FR-01 through FR-10 only. P1/P2 remain out of scope. The recorded scope matches PRD v1 §1.2–1.3; no requirement changes were made.
- Source files: all three required paths were readable and copied to docs/source/; copied bytes were verified against source SHA-256.
- Existing work: preserved the empty task outputs/ and work/ directories; no Obsidian source was changed.
- Remote baseline: private zgj0707/wanwan-chatbot, branch main, initial commit verified as 43e9fc778bbfcbeaad6dc68264af22ff682cb720; configured as origin.
- Python: selected 3.12.14. No Python installation was performed.
- Acceptance evidence: GitHub connector metadata/tree/commit reads; local Git blob/tree/commit SHA checks; source readability and SHA-256 comparisons.
- Not run: application startup, dependency installation/lock, tests, model calls. These belong to later tasks; no real API credentials were supplied.

## T01 — skeleton, configuration, clock, and FakeClient

- Status: PASS.
- Inputs: local YAML configuration, profile name/messages/operation ID/deadline, and timezone-aware clock values. Outputs: strict AppConfig, LLMResult, health/settings responses. Failures include malformed or extra config, invalid endpoint/timezone/range, missing real-mode key, upstream auth/network/timeout errors, and invalid JSON.
- Implemented the minimal app package, settings and health routes, real-client boundary, deterministic FakeClient injection, cross-process instance lock, startup/setup scripts, config examples, and complete direct/transitive dependency lock.
- Fake mode remains offline. The FakeClient test replaces OpenAI construction with a failure guard; health and settings startup tests made zero model calls. Real mode without SUWAN_API_KEY starts, serves settings/health, and returns CONFIG_MISSING from chat.
- Dependency installation: PASS with CPython 3.12.14; pip 25.0.1; FastAPI 0.142.2; Uvicorn 0.54.0; Pydantic 2.13.5; OpenAI 2.54.0; SQLite 3.53.1. Exact dependency versions are recorded in requirements.lock.txt.
- Commands and results:
  - python -m pytest -q — PASS, 23 passed. One StarletteDeprecationWarning notes its TestClient is deprecating the httpx integration; tests still pass.
  - python -m pip check — PASS, no broken requirements.
  - setup.bat — PASS, lock install and pip check completed.
- Scope held: no database schema/content, chat UI, or world events were added. No real API calls were made.
- Not run: live provider authentication/behavior; T12 requires user credentials and separate authorization.

## T02 — database schema, seed, transactions, and repository

- Status: PASS.
- Added all 14 PRD tables with SQL checks, foreign keys, unique constraints, eight required indexes, WAL/FULL/busy-timeout/foreign-key pragmas, and strict schema version refusal. Existing v1 databases are verified and never reseeded; an unversioned database with existing tables is left untouched and rejected.
- Added the PRD v1 persona seed and voice rules. Pydantic validates the fixed identity anchors and initial preferences before canonical JSON is persisted; the seed records SHA-256 hashes for the three immutable source documents. The character state starts at valence 0/energy 60 and relationship closeness 10. The three life items are labeled as product seeds, each planned at step/cycle 0; no events or prior experiences are created.
- Added required metadata including a UUID4 installation ID, repository transactions with rollback, application-layer session reference validation, a nonblocking process-wide mutation lock, and pending-operation recovery to interrupted/PROCESS_INTERRUPTED with request text cleared. Startup lifecycle wiring remains scheduled for T04, where the app lifespan is in scope.
- Corrected the T01 instance-lock path in `app/main.py` from `data/instance.lock` to the PRD-required `data/app.lock`; this was necessary for the OS lock implementation to protect the specified path.
- Commands and results:
  - `python -m pytest tests/test_repository.py -q` — PASS, 10 passed. Covers idempotent init/preservation, schema/index/pragmas, exact seeds and metadata, SQL and app-level foreign-key checks, uniqueness, transaction rollback, same-process and cross-process instance locking, nonblocking mutation lock, schema-version refusal, and pending recovery.
  - `python -m pytest tests/test_config.py -q` — PASS, 23 passed. One existing Starlette deprecation warning about the `httpx` TestClient integration remains.
- No real model calls, fictional life events, or changes to the Obsidian source documents.

## T03 — state, prompt, and model-call boundary

- Status: PASS.
- Added strict analysis JSON models for state deltas, fact candidates, and persona proposals. Integer and boolean types are strict; delta ranges, list counts, key whitelist, birthday MM-DD validity, and current-user evidence substrings are checked. Invalid analysis is represented as a neutral result with `ANALYSIS_DEGRADED`; the low-level client performs no JSON repair call.
- Added pure state projection for complete-hour valence/energy decay, retained sub-hour remainder, clock-backward warning, relationship guard expiration/extension, database row validation, diagnostic mood, and character/display timezone context. State remains global across sessions; projections do not mutate database rows.
- Added prompt assembly in PRD priority order, with confirmed data separated from instructions, exactly three life threads, committed-event filtering, complete-turn filtering, an assistant-only initiative history form, required ID references, UTF-8 byte-plus-message-overhead budgeting, and eviction in history → lowest-score fact → oldest event order. Required content that still exceeds budget raises `CONTEXT_TOO_LARGE`/413 before a model call.
- Completed the LLM boundary: input budgets are checked for every profile; analysis/world use JSON mode and strict validation; chat never requests JSON mode. The SDK retry is disabled. Chat transient network/408/429/5xx retry and format repair share a two-attempt cap; 400/401/403/404 do not retry, and no retry begins with under five seconds left. Blank, overlength, fenced, and generic assistant-opening replies get at most one repair attempt. FakeClient remains offline and supports deterministic faults.
- Added per-call metadata metrics with nullable token counts and a repository sink that writes in a short, independent transaction. Metrics contain no prompt/reply text. Network/model calls occur outside SQLite transactions.
- Commands and results:
  - `python -m pytest tests/test_state.py tests/test_config.py tests/test_repository.py -q` — PASS, 94 passed. One existing Starlette deprecation warning about its `httpx` TestClient integration remains.
- No live provider calls were made; real-SDK request behavior was checked with a stub, and T12 remains separate authorization-gated work.

## T04 — chat loop and minimal UI

- Status: PASS.
- Implemented the ordered, non-streaming chat submission flow with canonical request hashing, operation ID conflict checks, success replay from current database messages, pending status responses, explicit same-ID retry, and a nonblocking process-wide mutation lock. Session activity is checked before an operation is created; no provider request is made when real-mode chat or analysis credentials are missing.
- Analysis is called once. Invalid or unavailable analysis degrades to a neutral delta and warning. At T04 completion all fact and persona candidates were discarded; T05 subsequently connected fact and persona proposal persistence. Chat runs outside SQLite transactions and is limited by the T03 retry/repair cap. A failed reply clears request text and leaves messages and relationship/character state untouched. Successful user+assistant messages, state changes, prompt reference IDs, operation result metadata, and session timestamp commit in one short transaction.
- Added startup database initialization and pending-operation recovery to interrupted/PROCESS_INTERRUPTED, plus `/api/v1/health`, sessions create/list/archive/restore, paginated message history, chat, and operation status routes. API requests use a random in-memory 256-bit token except the health endpoint; Host and Origin are restricted to the two loopback origins; static files use a whitelist; user text is rendered through `textContent`.
- Added the three-file minimal UI with session navigation, archive/restore, pending and failed bubbles, same-operation retry and status lookup, 65-second request timeout, cancelable browser wait, Tokyo time context, and Enter/Shift+Enter handling that respects IME composition.
- README now documents that a process crash after a provider reply but before local commit can lead to a second provider charge on retry, while the local turn is committed at most once.
- Commands and results:
  - `python -m pytest -q` — PASS, 108 passed. One Starlette deprecation warning remains for the installed TestClient/httpx integration.
  - `python -m pip check` — PASS, no broken requirements.
  - `python -m compileall -q app tests` — PASS.
  - `node --check ui/app.js` — PASS.
  - `git diff --check` — PASS.
- No real provider calls were made.

## T05 — user memory loop

- Status: PASS.
- Added the seven-key user-fact whitelist, privacy/value validation, explicit nickname evidence, valid MM-DD birthdays including 02-29, NFKC/lower/punctuation normalization, canonical hashes, bigram Dice retrieval, deterministic top-k ordering, and preferred-name fallback. Invalid active memory data disables memory retrieval with `MEMORY_UNAVAILABLE` while chat continues.
- The successful chat transaction now stores only pending user-fact/persona proposals with a current user-message source. Candidate quotes must be exact substrings of that message and contain the proposed value; assistant text is never recorded as a source. Duplicate pending facts merge, active same-value facts do not produce proposals, and conflicts create a new version only after explicit acceptance.
- Added memory listing, manual add, versioned edit, forget, proposal listing, and user-fact accept/reject APIs. Manual facts have no message source. Fact acceptance validates the extant user source and refuses invalid or redacted evidence. Persona proposal acceptance remains for T06.
- Forget runs transactionally across the whole same-key lineage, sanitizes/invalidate proposals, redacts source and referenced assistant messages, clears operation request text, optionally redacts every message in source sessions (default true), and iterates through other active facts supported by newly redacted user messages. `memory_epoch` increments once. Successful operation replay reads the current redacted message rows. UI redacted bubbles use the required removal text; the API and README disclose that older backups may restore the content.
- Commands and results:
  - `python -m pytest tests/test_memory.py -q` — PASS, 9 passed. Follow-up boundary checks cover zero-width-obfuscated phone numbers, memory candidate storage SQL failure, and the required SOURCE_REDACTED result after forget.
  - `python -m pytest -q` — PASS, 116 passed. One existing Starlette deprecation warning remains for the installed TestClient/httpx integration.
- Offline retrieval checks include 10 fixed Chinese query examples with 9/10 expected hits. This confirms the specified deterministic algorithm, not real-model semantic quality. No real provider calls were made.
- T07 added the browser management pages and interaction flow; persona proposal confirmation is available from both the memory manager and read-only persona archive.

## T06 — persona evolution

- Status: PASS.
- Added strict acceptance logic for `preferences.likes` and `preferences.dislikes` only. Values must be 1–30 characters, cannot contain obvious fixed-identity terms, avoid normalized duplicates, and cannot exceed 10 items per list. Rejected, malformed, full-list, or redacted-source proposals do not create a version.
- Acceptance copies the fixed anchor snapshot, voice text, and original source hashes into a new `persona_versions` row; only the requested preference array changes. Canonical anchor JSON is checked against the immutable seed before acceptance. A repeated accept returns the existing result; a later proposal for an already-present value also resolves without another version. Anchor tampering returns `ANCHOR_CHANGE_FORBIDDEN`.
- Added `GET /api/v1/persona` with version, anchors, preferences, voice summary, and creation time, plus persona proposal acceptance through the existing proposal resolver. Automatic proposal storage stays pending and source-linked. The route does not return provider credentials.
- Commands and results:
  - `python -m pytest tests/test_persona.py tests/test_memory.py -q` — PASS, 12 passed.
  - `python -m pytest -q` — PASS, 120 passed. One existing Starlette deprecation warning remains for the installed TestClient/httpx integration.
  - `python -m pip check` — PASS, no broken requirements.
  - `python -m compileall -q app tests`, `node --check ui/app.js`, and `git diff --check` — PASS.
- No real provider calls were made.

## T07 — management pages and diagnostics

- Status: PASS.
- Added the three-section memory manager, source and timestamp trace, pending user-fact and persona controls, immutable persona archive, diagnostics, recent call metrics, and a single-attempt model probe with an explicit cost notice. All user- and model-provided values are rendered as text nodes.
- Added diagnostic responses that show only mode, provider host/model identifiers, credential-configured state, aggregate metrics, and sanitized per-call metadata. They never return configured secret values. The probe stores no test content and does not retry.
- The browser acceptance path completed pending-fact confirmation, new-version edit, forget with source-session redaction, and a fresh chat. The fresh reply referenced no fact; forgotten rows retained no value. An HTML-like memory value appeared as literal text in the rendered card. Diagnostics displayed fake mode and profile status without credentials.
- Commands and results:
  - `python -m pytest -q` — PASS, 124 passed. One existing Starlette deprecation warning remains for the installed TestClient/httpx integration.
  - `python -m pip check` — PASS.
  - `python -m compileall -q app tests`, `node --check ui/app.js`, and `git diff --check` — PASS.
- No real provider calls were made.

## T08 — bounded world events and page-based initiative

- Status: PASS.
- Added a bounded world-event pipeline over the three seeded life items. It considers fixed time slots and a limited recent window, creates at most one event per advance, stores item snapshots and event references, and supports retracting only the latest event with dependent reply cleanup. Invalid model output or a timeout uses a fixed fallback; world generation has no retry loop or background scheduler.
- Added initiative eligibility checks for quiet hours, disabled state, guard, energy, cooldown, and available events. A successful initiative commits one assistant message and an operation result; operation replay does not call the model again. The six-hour cooldown is shared across sessions, and failed attempts do not consume it.
- World update timing and warnings are persisted on success, skip, failure, and replay. The world cursor is committed using the time after generation completes, preventing model latency from advancing the cursor against a stale timestamp.
- Final suite evidence includes duplicate-slot idempotency, bounded catch-up, no-eligible-slot cursor advance without an event, paused/completed items, fallback behavior, retraction dependency checks, and initiative replay/cooldown cases.
- No real provider calls were made.

## T09 — consistent backups and offline restore

- Status: PASS.
- Added SQLite online-backup snapshots, a versioned ZIP manifest with SHA-256 and row counts, export through the token-protected API, and a settings-page download action. Archives contain only `app.sqlite3` and `manifest.json`.
- Added strict restore validation for archive entries, sizes, hashes, schema/version, installation identity, SQLite integrity, and foreign keys. Restore takes the same application lock, creates a rollback database snapshot, replaces only the database, and reapplies the rollback snapshot if post-replacement validation fails. `.env` and `config.yaml` are preserved.
- Added `scripts/restore_backup.py` with an exact `RESTORE` interactive confirmation and an explicit `--yes` option for automated use. Tests cover complete round-trip database state, unsafe/corrupt archives, lock refusal, API token checks, and export cleanup.
- No real provider calls were made.

## T10 — offline acceptance and fault coverage

- Status: PASS.
- Added all 12 PRD §16.3 behavior-review scenarios to `tests/fixtures/behavior_cases.json`. They remain `NOT_RUN` because fake responses cannot establish real-model persona quality.
- Added stress and fault coverage for successful operation replay, shared state across 20 sessions, seven atomic chat-commit fault points, 100 user-origin candidate inputs remaining pending, global initiative cooldown, failure not consuming cooldown, and world advancement with no eligible item.
- Final automated run: `.venv\Scripts\python.exe -m pytest -q` — **151 passed**, with one Starlette/httpx TestClient deprecation warning, in 14.59 seconds.
- No real provider calls were made.

## T11 — clean install, restart, browser acceptance, and handoff docs

- Status: PASS.
- Rewrote README with clean installation, startup, fake/real mode, data and memory boundaries, bounded world behavior, backup and recovery, operational limits, and development checks. Port 8000 collision now returns a Chinese diagnostic and exit code 3.
- Clean-install acceptance copied source into a separate empty directory, installed locked dependencies with CPython 3.12.14, started the fake-mode app, completed a chat, restarted, and confirmed the session and messages persisted. A second-start check verified the port collision message.
- Browser acceptance confirmed seeded life items, no invented initial events, backup instructions, and a generated ZIP containing exactly the database and manifest. T07 browser memory controls remained covered by the prior acceptance record.
- Removed the temporary clean-install tree and synthetic acceptance data after verification. No real credentials were read or used.
- Quality checks: `pip check` reported no broken requirements; `compileall`, `node --check ui/app.js`, and `git diff --check` passed. Git emitted only a README line-ending conversion warning during `git diff --check`.

## Remaining work

- T12 technical evaluation is complete. The PRD's structured-output and 19-turn thresholds have real-provider evidence; the cost remains unknown because there is no configured price card.
- User review of the 14 behavior cases remains NOT_RUN until actual replies can be reviewed. A focused live follow-up is still needed for direct memory recall, virtual identity disclosure, fixed identity under override, and action-aside cleanup.
- See [acceptance.md](acceptance.md) for exact batch counts, thresholds, and known limits. Do not treat retrieval hits as proof that the model will always repeat the fact correctly.

## T12 conversation-quality follow-up

- Added targeted repair reasons for a direct virtual-identity question, an explicit identity rewrite request, and a direct question where a matching confirmed fact is injected but the reply still says it is forgotten. The existing reply limit remains two chat attempts per turn.
- Broadened the parenthetical stage-direction cleanup vocabulary and strengthened the conversational prompt and voice rules.
- `py_compile` passed for the four modified Python modules; eleven focused synthetic policy assertions passed. No real-provider calls, full test suite, or manual human sign-off has been performed; all behavior fixture results stay NOT_RUN.
