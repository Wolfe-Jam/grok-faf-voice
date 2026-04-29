# Changelog

All notable changes to **grok-faf-voice** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] — 2026-04-29 — Voice Memory Layer (VML) brand + onboarding

### Changed (breaking — pre-1.0, no users)

- **Renamed `token` → `api_key` across the SDK surface.** Matches
  the FAF / xAI / LiveKit ecosystem convention (`XAI_API_KEY`,
  `LIVEKIT_API_KEY`). The credential is long-lived and pasted into
  `.env` — that's API-key shape, not session-token shape. Concrete
  changes:
  - Env var: `MCPAAS_TOKEN` → **`MCPAAS_API_KEY`**
  - Constructor kwarg: `FAFMemory(soul, token=...)` → **`FAFMemory(soul, api_key=...)`**
  - Internal attr: `self._token` → `self._api_key`
  - Friendly error text: "Voice key" → "Voice API key"
  - The MCP wire field stays `token` for now (server-side contract);
    the SDK passes the api_key value through that field until
    MCPaaS server-side renames. A code comment in `etch()` flags this.
  No deprecation alias — the package was pre-1.0 with zero installed
  users at rename time.

### Added

- **Voice Memory Layer (VML)** — coined as the FAF-family term for
  the voice-memory persistence category. File extension is `.fafm`
  (not `.vml` — keeps the FAF family cohesive: `.faf` / `.fafb` /
  `.fafm`). Media type `application/vnd.fafm+yaml` is planned for
  IANA registration. This SDK is positioned as the reference
  implementation of VML.
- **`.fafm 🐘🎙️` family mark** — voice variant of the `.faf 🐘`
  family mark. Microphone modifier qualifies the voice surface
  (mirrors Grok's earned ⚡️ pattern in `grok-faf-mcp`).
- **`ONBOARDING.md`** — five-minute one-pager. System requirements
  (4 keys + 1 namepoint) preamble, then four numbered steps
  (clone+venv → install+configure → verify with WJTTC → talk to
  your agent). PR conventions footer.
- **README polish (Bronze → Silver)**:
  - Badges row (PyPI, Python, License, Tests) — FAF cyan (#00D4D4)
    on metadata badges, MIT-yellow on License, dynamic on Tests.
  - One-line problem statement: "Voice agents forget. This fixes it."
  - "Three files, three audiences" reader-tree (`pyproject.toml` /
    `project.faf` / `README.md` / `grok_faf_voice/`).
  - "Two layers, one ecosystem" diagram pairing `.faf` (Foundational
    Context Layer) with `.fafm` (Voice Memory Layer / VML).
  - Quickstart split into Minimal (10-line) + Full integration.
  - `Contributing` section linking ONBOARDING.md + WJTTC.md.
  - Ecosystem star link (points at `Wolfe-Jam/faf-cli` per the
    star-concentration strategy; sits in Lineage footer).

### Changed

- **`pyproject.toml` description** shifted to sibling's pipe-bullet
  rhythm: `grok-faf-voice | VML • Voice Memory Layer for Grok •
  Persistent across sessions, devices, models • LiveKit-enabled`.
- **Keywords trimmed** 13 → 10 high-signal terms across 5 question-
  types: brand (`voice-memory-layer`, `vml`, `fafm`), platforms
  (`grok`, `xai`, `livekit`), capability (`persistent-memory`,
  `voice-agent`), differentiator (`paralinguistic`), protocol
  (`mcp`). Dropped: `voice`, `memory`, `realtime` (too generic),
  `faf` (covered by `fafm` + package name).
- **License section** brand line promoted above MIT — "Don't copy
  FAF brand. Do your own." now reads first.
- **`__init__.py` module docstring** updated to position the SDK as
  the VML reference implementation. `pip show` and IDE hover-help
  carry the term.
- **Stale `0.0.12` version reference** removed from the
  "What it sounds like" dialog example. Narrative content is now
  timeless.

## [0.1.1] — 2026-04-29 — auto-merge session-reuse fix + WJTTC v1

### Fixed

- **Auto-merge no-op on session 2+ when `FAFMemory` is reused across
  sessions.** The `_merge_completed_this_session` guard flag was set
  on every `merge()` but never reset, so the README pattern
  (module-scope `mem` + per-session `entrypoint`) caused every session
  after the first to skip its shutdown merge. Fix: `attach_auto_merge`
  now resets `_merge_completed_this_session` and `_merge_in_progress`
  to `False` at registration time, so each session's shutdown callback
  starts from a clean slate. Regression test added
  (`test_attach_auto_merge_resets_flags_for_session_reuse`).

### Added

- **WJTTC test regime (v1)** — F1-inspired four-tier early-warning
  system against fast-moving upstreams (xAI Voice, LiveKit Agents,
  MCPaaS, fastmcp). 🛡️ BRAKE (gating), ⚙️ ENGINE (correctness +
  upstream-contract pins), 🌀 AERO (polish), 🛞 TYRES (live probes,
  costs $, manual/cron). Single runnable: `./scripts/wjttc.sh
  [--tyres | --tyres-only]`. 15 contract-pin tests in
  `tests/test_wjttc_contracts.py` ride the standard pytest run, so
  upstream drift (LiveKit symbol renames, xAI strict-mode regressions,
  MCPaaS endpoint changes) goes red before users hit it. Auto-loads
  `.env`. Skip ≠ fail. Spec lives in `WJTTC.md`. Three artifacts ship
  with the package (the contract tests via sdist; the doc and shell
  script are dev-only, not in the wheel).

## [0.1.0] — 2026-04-27 — first public release

The complete first-public surface for **persistent memory in Grok
Voice agents**. Everything composes from one import.

### Seven primitives

- **`FAFContext`** — static project DNA. Loads `.faf`
  (`application/vnd.faf+yaml`, IANA-registered). Read once per session.
- **`FAFMemory`** — live voice memory. Reads/writes a soul on MCPaaS
  via the MCP protocol. Composes the scratchpad, ledger, and bus.
- **`Scratchpad`** — in-session ephemeral key/value with priority +
  smart-tag for the merge engine.
- **Paralinguistic Tags** — `mem.etch_paralinguistic(marker_type,
  value)` records HOW the user spoke (tone, emotional state, speaking
  style, interruption pattern). Surfaced via `paralinguistic_summary`
  for next-session awareness.
- **Smart Merge Engine** — `mem.merge(strategy=...)` promotes
  scratchpad → permanent soul. Strategies: `heuristic` (default, free,
  fast), `grok-decides` (LLM-judged via xAI structured outputs),
  `merge_all`.
- **Voice Session Ledger** — `VoiceSessionLedger` Protocol +
  `NullVoiceSessionLedger` (default) + `InMemoryVoiceSessionLedger`
  (process-scoped). Records every merge attempt for audit and
  cross-session resumption. Persistent backends are downstream.
- **Context Bus** — async pub/sub over voice-memory events with
  backpressure. 13 canonical event types (`scratchpad.updated`,
  `paralinguistic.detected`, `tool.about_to_run`, `tool.completed`,
  `merge.starting`, `merge.completed`, `session.resumed`, `audio.cue`,
  and more). Async-first `mem.bus.on(event, handler)` plus sync compat
  via `mem.bus.on_sync(...)`.

### Behaviors

- **Soul → prompt bridge** — `await mem.recall_for_prompt()` pre-loads
  prior soul into the agent's instructions at session start, so it
  opens with continuity. Falls back gracefully on empty soul or read
  failure — never raises.
- **Cross-session resumption** — `await mem.on_session_start(session)`
  silently retries any merge attempts that didn't complete in a prior
  session. The user only hears about it when severity is high
  (`retry_count ≥ 2` or many failed entries from a single attempt).
  72h default age-out, max 3 retries per attempt.
- **Reliable shutdown** — `mem.attach_auto_merge(session, ctx)`
  registers an awaitable shutdown callback on the LiveKit `JobContext`,
  so a multi-second merge finishes cleanly on every termination path
  (graceful close, user disconnect, room destroy, worker drain).
- **Tool latency discipline** — `LATENCY_BRIDGE_INSTRUCTIONS`
  system-prompt reinforcement plus per-tool `CRITICAL LATENCY RULE`
  docstrings keep the agent from going silent during tool calls.
  Multi-second tools (like `merge_now`) emit explicit `session.say()`
  verbal holds before running.
- **Global tool observability** —
  `enable_global_tool_bus(memory, agent)` gives the Bus full
  visibility over every tool the agent runs (FAFMemory's own four AND
  any user-defined tools). Idempotent.

### Built-in `@function_tool` wrappers

- `etch_memory` — save content to permanent soul
- `recall_memory` — read current soul state
- `note_paralinguistic` — record HOW the user is speaking
- `merge_now` — promote scratchpad → soul on user request

All four ship with explicit verbal-bridge instructions to keep
realtime conversation natural.

### Defaults

- xAI chat model: `grok-4-1-fast-reasoning` (Smart Merge judgment).
- Structured outputs via `response_format=json_schema` (strict) for
  the LLM merge path. No fallback parser needed.
- xAI Realtime turn detection: `server_vad` with tuned threshold +
  silence + prefix padding for natural turn-taking.

### Quality

- 112 tests passing locally; 3 additional `@pytest.mark.network`
  round-trips skip-by-default (opt in with `pytest --run-network`).
- Ruff clean across the package.
- Python 3.10–3.13 supported.

### License

MIT.

---

## Development history (pre-public)

The seven primitives above were built and shipped iteratively across
internal versions `0.0.1` → `0.0.12` over a short development cycle.
The development history is preserved below for transparency; first
public installers should treat `0.1.0` as the canonical entry point.

### `0.0.12` — soul → prompt bridge

`FAFMemory.recall_for_prompt(*, header, empty_message)` — async method
that fetches the soul and returns a string ready for direct injection
into Agent instructions. Strips MCPaaS server preamble. Returns the
configurable empty message on failure — never raises.
`@pytest.mark.network` skip-by-default via `--run-network` flag.

### `0.0.11` — `attach_auto_merge` JobContext fix

Shutdown callback API lives on `JobContext`, not `Agent`. Signature
corrected to `attach_auto_merge(session, ctx, *, strategy)`.

### `0.0.10` — global tool bus middleware

`enable_global_tool_bus(memory, agent)` wraps every tool (including
user-defined) with `tool.about_to_run` + `tool.completed` events.
Idempotent; preserves LiveKit `FunctionTool` metadata. Patches
`agent.update_tools` for dynamic additions.

### `0.0.9` — Context Bus

`ContextBus` class with 13 canonical `BusEvent` types, async-first
subscription, sync compat layer, backpressure, dispatcher error
isolation. Wired into `FAFMemory.etch`, `attach_auto_merge`,
`on_session_start`, and the four tool factories.

### `0.0.8` — cross-session resumption

`MergeAttempt` dataclass, ledger Protocol extensions
(`get_incomplete_merges`, `mark_merge_resumed`, idempotent
`log_merge_attempt`), `FAFMemory.on_session_start(session)` resumption
hook with silent-retry-default and high-severity user message.

### `0.0.7` — tool latency discipline + reliable shutdown

`LATENCY_BRIDGE_INSTRUCTIONS` constant, Pattern A docstrings on the
fast tools, Pattern B verbal hold on `merge_now`, new ledger module,
`attach_auto_merge` via awaitable shutdown callback. xAI default
chat model bumped to `grok-4-1-fast-reasoning` with structured
outputs.

### `0.0.x` — earlier

Smart Merge Engine, paralinguistic tags, etch + recall + Scratchpad,
the `FAFContext` + `FAFMemory` siblings, CI foundations, the
`utils.transcribe` xAI STT utility.

---

[0.1.2]: https://github.com/Wolfe-Jam/grok-faf-voice/releases/tag/v0.1.2
[0.1.1]: https://github.com/Wolfe-Jam/grok-faf-voice/releases/tag/v0.1.1
[0.1.0]: https://github.com/Wolfe-Jam/grok-faf-voice/releases/tag/v0.1.0
