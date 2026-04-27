# Changelog

All notable changes to **grok-faf-voice** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.11] — 2026-04-27

### Fixed — `attach_auto_merge` signature

`Agent.add_shutdown_callback` doesn't exist on LiveKit's actual
`Agent` class — that method lives on `JobContext`. Surfaced by a real
manual run; unit tests with a fake agent stand-in passed because the
fake mocked the API we expected, not the API that exists.

- `attach_auto_merge(session, ctx, *, strategy)` — second positional
  argument is now `ctx: JobContext` instead of `agent: Agent`. Inside,
  registers via `ctx.add_shutdown_callback(...)` (the real LiveKit
  API).
- Example updated: `mem.attach_auto_merge(session, ctx, strategy=...)`.
- Test fake renamed `_FakeAgent` → `_FakeJobContext` to make the API
  surface explicit.

This is a breaking signature change but no public consumers exist
(repo private, package never on PyPI) — fix is appropriate.

111 / 111 tests still passing. Ruff clean.

---

## [0.0.10] — 2026-04-27

### Added — global tool bus middleware

Bus coverage for **every** tool the agent runs, not just FAFMemory's
own. User-defined tools added to the agent's `tools=[]` list now fire
`bus.tool.about_to_run` + `bus.tool.completed` automatically — no
per-tool wrapping required.

- `enable_global_tool_bus(memory, agent)` — call once after Agent
  construction. Wraps every tool in `agent.tools` by mutating the
  underlying callable in place, preserving LiveKit `FunctionTool`
  metadata (`info.name`, `info.description`).
- Idempotent: tools tagged `_bus_wrapped=True` (the four FAFMemory
  factory outputs, or anything previously wrapped) are skipped.
  Calling `enable_global_tool_bus` twice is a safe no-op.
- Patches `agent.update_tools` so tools registered later via
  runtime tool updates are also wrapped automatically. The patch
  itself is idempotent — second call to `enable_global_tool_bus`
  doesn't re-wrap `update_tools`.
- `tool.completed` payload now includes `success: bool` (True on
  return, False on raised exception) plus `result` or `error`.
- Exceptions from wrapped tools are re-raised so LiveKit's error
  handling is preserved.

### Changed

- The four FAFMemory factories (`make_etch_tool`, `make_recall_tool`,
  `make_paralinguistic_tool`, `make_merge_tool`) no longer emit
  generic `tool.about_to_run` / `tool.completed` from inside their
  bodies — that's the global wrapper's job. Domain-specific events
  (`soul.updated` from `mem.etch`, `paralinguistic.detected` from
  `make_paralinguistic_tool`, `merge.starting` + `merge.completed`
  from `make_merge_tool`) are still emitted from inside the factory
  bodies so they fire even without `enable_global_tool_bus`.
- Factory output is tagged `_bus_wrapped=True` so the global wrapper
  skips re-wrapping FAFMemory tools (clean separation: generic tool
  events = global wrapper's job, domain events = per-factory's job).

### Tests

- 110 / 110 passing (was 102). 8 new tests cover global wrapper
  pre/post emission, error path with success=False + re-raise,
  factory tools skipped via `_bus_wrapped`, idempotent double call,
  metadata preservation (`info.name` + `info.description`),
  `update_tools` patched for future additions, `update_tools` patch
  itself idempotent, factory domain-events still fire after global
  wrap.

---

## [0.0.9] — 2026-04-27

### Added — Context Bus

Async pub/sub over voice-memory events. Gives developers precise,
semantic control over the voice memory layer with full async power
and backpressure — the right hooks at the right moments.

- `ContextBus` class — async-first dispatcher with a single
  background task and bounded `asyncio.Queue` (default cap 1000).
  Backpressure: queue-full drops the event with a logged warning;
  the producer never blocks.
- `BusEvent` — Enum of 13 canonical event types (`scratchpad.updated`,
  `scratchpad.dirty`, `soul.updated`, `memory.snapshot`,
  `paralinguistic.detected`, `tool.about_to_run`, `tool.completed`,
  `merge.pending`, `merge.starting`, `merge.completed`,
  `session.resumed`, `context.invalidated`, `audio.cue`).
- `BusEventPayload` — Pydantic envelope (event + payload + timestamp + source).
- `mem.bus.on(event, async_handler)` — async-first subscription. Also
  usable as a decorator (`@mem.bus.on(event)`).
- `mem.bus.on_sync(event, sync_handler)` — sync compat layer; the bus
  wraps the callback and dispatches it on the same task lifecycle.
- `mem.bus.off(event, handler)` — unsubscribe.
- Convenience emitters on the bus: `emit_scratchpad_updated`,
  `emit_tool_about_to_run`, `emit_tool_completed`,
  `emit_merge_starting`, `emit_merge_completed`,
  `emit_paralinguistic_detected`, `emit_session_resumed`.
- Failing handlers are logged at WARNING and never stop the
  dispatcher or other handlers for the same event.

### Wired into existing primitives

- `FAFMemory.__init__` accepts an optional `bus` kwarg; defaults to a
  fresh `ContextBus()`.
- `FAFMemory.bus` property exposes the bus.
- `FAFMemory.start_bus()` / `stop_bus()` for explicit lifecycle.
- `FAFMemory.etch()` now publishes `soul.updated` on success.
- `attach_auto_merge` publishes `merge.starting` and `merge.completed`
  (or `merge.completed` with `error` on the failure path) inside the
  shutdown callback.
- `on_session_start` publishes `session.resumed` after the resumption
  pass completes.
- `make_etch_tool` / `make_recall_tool` / `make_paralinguistic_tool` /
  `make_merge_tool` factories accept an optional `bus` arg (defaults
  to `mem.bus`) and emit `tool.about_to_run` + `tool.completed` around
  the tool body. `make_paralinguistic_tool` also emits
  `paralinguistic.detected` on the success path.

### Tests

- 102 / 102 passing (was 85). 17 new tests cover bus lifecycle
  (idempotent start/stop, auto-start on emit), async + sync + decorator
  subscription, multi-handler fan-out, event isolation, off()
  unsubscription, raising-handler isolation with logging, queue-full
  backpressure with warning, FAFMemory.bus exposure, bus injection via
  constructor, convenience emitters, and tool-call payload shape.

---

## [0.0.8] — 2026-04-27

### Added — cross-session resumption

Incomplete merges from prior sessions are silently retried on session
start. The user only hears about it when severity is high
(`retry_count ≥ 2` or many failed entries from a single attempt).

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

## [0.0.7] — 2026-04-27

### Added — tool latency discipline + reliable shutdown

The realtime stream goes silent during tool execution, and
fire-and-forget shutdown can lose data. This release adds explicit
verbal bridges for fast tools, a `session.say()` hold for the
multi-second merge, and an awaitable shutdown callback.

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

- `XAI_CHAT_MODEL_DEFAULT` → `grok-4-1-fast-reasoning`.
- `_grok_decides_split` now uses xAI structured outputs
  (`response_format=json_schema`, strict). Markdown-fence parsing +
  `try/except json.loads` fallback removed.
- New `grok_faf_voice/_merge_models.py` — Pydantic models
  (`MergePayloadEntry`, `MergeDecision`, `MergeResult`) + system /
  user prompt templates.
- Three-action merge result (`promote` / `keep_ephemeral` /
  `merge_into` collapsed to `promote` for now).
- `FAFMemory.merge()` extended return shape: adds `merged`,
  `kept_ephemeral`, `overall_notes` keys (back-compat preserved).
- `FAFMemory.tools(session)` and `make_merge_tool(mem, session)` now
  require a session for the verbal hold.
- `session.on("close")` retained for observability only.

### Tests

- 74 / 74 passing (was 26). New tests cover structured-output merge
  paths, latency-pattern docstrings, shutdown-contract edge cases
  (ledger writes on success and failure, completion-flag no-op,
  session-without-id defensive guard), and the extended `merge()`
  return shape across all strategies.

---

## [0.0.x] — earlier

### Smart Merge Engine

`FAFMemory.merge(strategy=...)` promotes scratchpad entries to
permanent soul memory at session end. Strategies: `heuristic`
(default, free, fast), `grok-decides` (LLM-judged), `merge_all`.

### Level-2 utility — `grok_faf_voice.utils.transcribe`

xAI STT direct REST endpoint with `ffmpeg` pre-extract — works around
the LiveKit STT wrapper's hardcoded 30s timeout. Library + CLI entry.

### Paralinguistic Tags

`FAFMemory.etch_paralinguistic` records HOW the user spoke (tone,
emotional state, speaking style, interruption pattern).
`paralinguistic_summary` surfaces recent markers for next-session
awareness.

### Etch + recall + Scratchpad + LiveKit tools

The minimal voice memory layer. `mem.etch(content)` writes durably to
MCPaaS via the MCP protocol; `mem.get()` reads back. `Scratchpad`
exposes in-session key/value with priority + smart-tag.
`mem.tools()` returns `@function_tool` wrappers ready to attach to a
LiveKit `Agent`.

### FAFContext + FAFMemory siblings

The two first-class objects of the SDK. Five-line install works.

### Foundations

CI matrix + ruff + coverage + conftest fixtures — tests are the only
gate.

---

[0.0.11]: https://github.com/Wolfe-Jam/grok-faf-voice/compare/v0.0.10...v0.0.11
[0.0.10]: https://github.com/Wolfe-Jam/grok-faf-voice/compare/v0.0.9...v0.0.10
[0.0.9]: https://github.com/Wolfe-Jam/grok-faf-voice/compare/v0.0.8...v0.0.9
[0.0.8]: https://github.com/Wolfe-Jam/grok-faf-voice/compare/v0.0.7...v0.0.8
[0.0.7]: https://github.com/Wolfe-Jam/grok-faf-voice/compare/v0.0.6...v0.0.7
