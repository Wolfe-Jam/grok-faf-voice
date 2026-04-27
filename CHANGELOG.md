# Changelog

All notable changes to **grok-faf-voice** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.8] — 2026-04-27 — Gate 5

### Added — cross-session resumption

The fifth Voice Memory Layer gap closed. Incomplete merges from prior
sessions are silently retried on session start. 95% of recoveries are
invisible; the user only hears about it when severity is high.

- `MergeAttempt` dataclass — full schema for audit + resumption.
- `VoiceSessionLedger.log_merge_attempt` now returns
  `merge_attempt_id` and supports idempotent updates by id.
- `VoiceSessionLedger.get_incomplete_merges(soul, max_age_hours=72)`
  — filters by soul + status + age window.
- `VoiceSessionLedger.mark_merge_resumed(merge_attempt_id, new_status)`
  — flags `in_progress` before retry.
- `FAFMemory.on_session_start(session, *, max_age_hours, strategy)` —
  the resumption hook. Default: silent retry. High-severity surface
  ("I had a couple of memories from last time…") triggers when
  `retry_count ≥ 2` or `failed_entry_ids > 5`. 72h default age-out,
  max 3 retries per attempt.
- Status string constants exported (`STATUS_COMPLETED`,
  `STATUS_PARTIAL_OR_FAILED`, `STATUS_IN_PROGRESS`, etc.).

### Changed

- `attach_auto_merge` now passes `soul=` to `log_merge_attempt` so
  `get_incomplete_merges` can find prior failures by soul.
- `examples/hello_grok_with_etch.py` adds
  `await mem.on_session_start(session)` after `session.start(...)`.

### Tests

- 85 / 85 passing (was 74). 11 new tests cover idempotent updates,
  soul + status + age-window filtering, silent vs high-severity retry
  paths, max-retry abandonment, retry-itself-fails-cleanly, broken
  `session.say()` doesn't block recovery.

---

## [0.0.7] — 2026-04-27 — Gate 4.5

### Added — tool latency discipline + reliable shutdown

Closes the SuperGrok 4.3 beta consult (Q12–Q15b) into one cohesive
ship. The realtime stream goes silent during tool execution, and
fire-and-forget shutdown loses data.

- `LATENCY_BRIDGE_INSTRUCTIONS` constant — system-prompt reinforcement
  string that backstops per-tool docstrings.
- Pattern A docstrings (`CRITICAL LATENCY RULE` block) on
  `etch_memory` / `recall_memory` / `note_paralinguistic` — instructs
  the model to speak a short bridge ("Got it.", "Let me check…")
  before calling.
- Pattern B verbal hold for `make_merge_tool(mem, session)` —
  explicit `session.say("Give me just a moment…")` before the
  multi-second merge, plus a closing confirmation.
- New `grok_faf_voice/ledger.py` with `VoiceSessionLedger` Protocol
  + `NullVoiceSessionLedger` (default) + `InMemoryVoiceSessionLedger`.
- `FAFMemory.attach_auto_merge(session, agent)` registers an awaitable
  shutdown callback via `agent.add_shutdown_callback` — the worker
  awaits up to `shutdown_process_timeout` (~10s default), so a
  multi-second merge finishes cleanly on every termination path.
- `asyncio.Lock` + `_merge_in_progress` + `_merge_completed_this_session`
  flags compose with explicit `merge_now` (no double-merge, race-safe).
- README latency band table for SDK consumers writing custom tools.
- `xai.realtime.RealtimeModel` example wires `turn_detection`
  (`server_vad` defaults).

### Changed

- `XAI_CHAT_MODEL_DEFAULT` → `grok-4-1-fast-reasoning` (per Q12).
- `_grok_decides_split` now uses xAI structured outputs
  (`response_format=json_schema`, strict). Markdown-fence parsing +
  `try/except json.loads` fallback removed.
- New `grok_faf_voice/_merge_models.py` — Pydantic models
  (`MergePayloadEntry`, `MergeDecision`, `MergeResult`) + system /
  user prompt templates.
- Three-action result (`promote` / `keep_ephemeral` /
  `merge_into`-as-promote at Gate 4.5; true consolidation at Gate 5+).
- `FAFMemory.merge()` extended return shape: adds `merged`,
  `kept_ephemeral`, `overall_notes` keys (back-compat preserved).
- `FAFMemory.tools(session)` and `make_merge_tool(mem, session)` now
  require a session for the verbal hold.
- `session.on("close")` retained for observability only.

### Tests

- 74 / 74 passing (was 26). 48 new tests cover Q12 structured outputs,
  Q14/Q14b latency patterns, Q15/Q15b shutdown contract — including
  edge cases for ledger writes on success and failure, completion-flag
  no-op, session-without-id defensive guard, and merge() six-key
  shape across all three strategies.

---

## [0.0.x] — earlier — Gates 0-4

### Gate 4 — Smart Merge Engine

`FAFMemory.merge(strategy=...)` promotes scratchpad → permanent soul
memory at session end. Strategies: `heuristic` (default, free, fast),
`grok-decides` (LLM-judged), `merge_all`. The third of five MCP-gaps
shipped.

### Level-2 utility — `grok_faf_voice.utils.transcribe`

xAI STT direct REST endpoint with `ffmpeg` pre-extract — works around
LiveKit STT wrapper's hardcoded 30s timeout. Library + CLI entry.

### Gate 3 — Paralinguistic Tags

`FAFMemory.etch_paralinguistic` records HOW the user spoke (tone,
emotional state, speaking style, interruption pattern). The most
demonstrable MCP gap. `paralinguistic_summary` surfaces recent
markers for next-session awareness.

### Gate 2 — FAFMemory etch + recall + Scratchpad + LiveKit tools

The minimal voice memory layer. `mem.etch(content)` writes durably to
MCPaaS via the MCP protocol; `mem.get()` reads back. `Scratchpad`
exposes in-session key/value with priority + smart-tag for Gate 4.
`mem.tools()` returns `etch_memory` + `recall_memory` `@function_tool`
wrappers ready to attach to a LiveKit `Agent`.

### Gate 1 — FAFContext + FAFMemory siblings

The two first-class objects of the SDK. Five-line install works.

### Gate 0 — purposeful rebuild

Reset from v0.2.0 scaffolding to ship the Voice Memory Layer thesis
on its own terms. CI matrix + ruff + coverage + conftest fixtures —
tests are the only gate.

---

[0.0.8]: https://github.com/Wolfe-Jam/grok-faf-voice/compare/v0.0.7...v0.0.8
[0.0.7]: https://github.com/Wolfe-Jam/grok-faf-voice/compare/v0.0.6...v0.0.7
