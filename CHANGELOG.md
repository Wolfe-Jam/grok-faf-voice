# Changelog

All notable changes to **grok-faf-voice** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2] — 2026-05-22 — Sibling cross-link

Two profiles, one `.fafm` format — the voice and knowledge reference
implementations now point at each other.

### Changed
- README + pyproject: cross-link [claude-fafm-sdk](https://pypi.org/project/claude-fafm-sdk/) (the knowledge profile). First PyPI release to carry the 0.3.0 (local souls / cross-vendor read) and 0.3.1 (parsed accessors) work — PyPI was last at 0.2.2.

## [0.3.1] — 2026-05-21 — Parsed accessors: `.facts` / `.profile` / `.index`

Typed views over a loaded soul, so you don't have to parse the YAML yourself.
Companion to v0.3.0's local souls — makes inspecting a soul (especially a
cross-vendor knowledge soul) a one-liner.

### Added

- **`FAFMemory.profile`** — the soul's profile (`"voice"` default | `"knowledge"`).
- **`FAFMemory.facts`** — the parsed `memory.facts` list (bare strings,
  `{text, tags?}`, or rich knowledge objects — per the `.fafm` spec).
- **`FAFMemory.index`** — the top-level `index` list (knowledge profile).

Lazy + cached: local mode (`from_file`) parses the file directly with no
`await`; MCPaaS mode reads the cache populated by a prior `await get()` (raises
`FAFRecallError` with a clear message if called before any read). The MCPaaS
server preamble (before the first `\n---\n`) is stripped before parsing,
mirroring `recall_for_prompt`.

### Changed

- `get()` now caches the soul text it returns (local + MCPaaS) so the accessors
  don't re-fetch.
- **`pyyaml>=6.0`** is now a declared direct dependency (was transitive). The
  accessors parse soul YAML; relying on a transitive dep was silent-drift risk.

## [0.3.0] — 2026-05-21 — Local souls: read/write `.fafm` off disk (no MCPaaS)

Adds a local-first path to `FAFMemory`: read and write `.fafm` documents
straight off disk — no MCPaaS, no API key. The companion to the MCPaaS-backed
constructor, and the capability that powers cross-vendor interop (loading souls
written by other FAF-family tools, e.g. a Claude-side knowledge soul).

Purely additive and backward-compatible: `local_path` defaults to `None`, so
existing MCPaaS behavior is unchanged.

### Added

- **`FAFMemory.from_file(path)`** (classmethod) — load a soul from a local
  `.fafm` file into a `FAFMemory` in local mode. Extracts the soul identifier
  from the document's `namepoint` via a lightweight regex (no new YAML
  dependency). Works with any profile (`voice` or `knowledge`).
- **`FAFMemory.to_file(path)`** — write the current soul body to a local
  `.fafm` file (byte-identical roundtrip with `from_file`). Handy for backups,
  inspection, or handing a soul to another tool.
- **`local_path` constructor param** — when set, `get()` reads from disk
  instead of MCPaaS. Defaults to `None` (MCPaaS).

### Why

Cross-vendor memory interop: a `.fafm` soul written by Claude-side tooling
(knowledge profile, per `application/vnd.fafm+yaml` v1.1) loads cleanly via
`from_file()` → `get()` → `recall_for_prompt()`, offline. The recall path's
"no `\n---\n` separator → use whole text" leniency means pure-YAML `.fafm`
documents flow through with no special-casing.

### Fixed

- Version coherence: `__init__.__version__` (was 0.2.1) and `pyproject.toml`
  (was 0.2.2) now both report 0.3.0.

## [0.2.2] — 2026-05-11 — Sessionless MCP co-existence (X-MCP-Mode: flexi)

Hotfix for mcpaas-cf upstream Sessionless MCP compliance (SEP-2567 + SEP-2575).
mcpaas.live/mcp now defaults to 'strict' mode — requires Mcp-Method +
MCP-Protocol-Version headers on every request. `fastmcp.Client(URL)` doesn't
send these by default, so `FAFMemory.etch()` and `FAFMemory.get()` would 400
against the live endpoint once mcpaas-cf deploys.

This release wraps the underlying fastmcp `Client` with an explicit
`StreamableHttpTransport(headers={"X-MCP-Mode": "flexi"})`, opting into
mcpaas-cf's co-existence mode (validate headers when present, accept when
absent). Behavior of `FAFMemory` is unchanged; the wire just carries one
additional header now.

### Fixed

- **`FAFMemory.etch()` and `FAFMemory.get()`** now construct an explicit
  `StreamableHttpTransport` with `X-MCP-Mode: flexi` header. Without this,
  mcpaas-cf strict-default rejects the call with `400 -32001 HeaderMismatch`.
- Companion fix in `faf-agent-mcp` v0.1.4.

### Added

- 3 WJTTC Test Shadow assertions in `tests/test_memory.py`.

### Verified

- 65/65 tests passing (62 existing + 3 new mode-header).
- Live smoke: `FAFMemory.get("grok")` returns soul body against local
  strict-default mcpaas-cf (wrangler dev :8787).

### Doctrine

When `fastmcp` upstream ships full spec-header support (Mcp-Method,
MCP-Protocol-Version), the `X-MCP-Mode: flexi` opt-in can be dropped.
Until then, this is the bridge.

---

## [0.2.1] — 2026-05-03 — Custom clone voice ID support

Hotfix for v0.2.0. v0.2.0's validator enforced "exactly 8 chars" per
the published xAI Custom Voices API docs ("voice_id: 8-character
lowercase alphanumeric identifier"). Real custom console clones return
**12 chars** (e.g. `vluy2u1jtsif`), so v0.2.0 rejected them at the
`VoiceAgent(voice=...)` constructor. Workaround in v0.2.0 was to use
the `CustomVoiceClient.text_to_speech()` path directly, which never
enforced the validator.

### Fixed

- **`VoiceAgent(voice=...)` now accepts both preset and custom clone
  voice IDs.** Validator updated to accept exactly **8 chars** (console
  presets, e.g. `355dca53` / `nlbqfwie`) **OR** exactly **12 chars**
  (custom console clones, e.g. `vluy2u1jtsif`). Discrete tiers, not a
  range — anything else (7, 9, 10, 11, 13+) is rejected.
- **Error message expanded** to mention both length tiers and link to
  console.x.ai for clone creation.

### Spec source

The two-length spec was confirmed by xAI on 2026-05-03 in a public
spec exchange (replying to wolfejam's v0.2.0 release thread on X).
Quoted from the answer:

> *"Custom console clones are currently 12 chars (presets remain 8).
> /v1/tts accepts both with no tier-specific restrictions."*

Capped at 12 chars — no plans to extend further.

### Notes

- v0.2.0's `CustomVoiceClient.text_to_speech()` path was unaffected and
  always accepted 12-char IDs (the strict-8 validator only fires on the
  `VoiceAgent` constructor). Anyone using v0.2.0 for TTS only could
  upgrade or stay; anyone wanting `VoiceAgent(voice="<12-char-clone>")`
  needs v0.2.1.
- No new dependencies. No breaking changes to v0.2.0 surface.

## [0.2.0] — 2026-05-01 — Custom Voice support

xAI announced their Custom Voice API on **2026-05-01**. This release
ships SDK-side support **48h later**, fulfilling the v0.2.0 Identity
roadmap slot — your voice is now part of your identity, alongside
your namepoint and Voice key.

### Added

- **`CustomVoiceClient`** — sync HTTP client for xAI's Custom Voices
  API. Voice CRUD (`create_voice`, `list_voices`, `get_voice`,
  `update_voice`, `delete_voice`, `download_reference_audio`) plus
  `text_to_speech` synthesis. Built on `httpx.Client` — no new
  dependencies.

  ```python
  from grok_faf_voice import CustomVoiceClient

  cv = CustomVoiceClient()  # uses $XAI_API_KEY

  # Clone a voice from a 90-120s WAV sample
  voice = cv.create_voice("sample.wav", name="My Clone", language="en")

  # Synthesize TTS with the cloned voice
  cv.text_to_speech(
      "Hello from the new voice",
      voice_id=voice["voice_id"],
      output_path="hello.mp3",
  )
  ```

- **Custom voice IDs accepted by `VoiceAgent(voice=...)`.** The
  validator now recognizes both the five built-in voices
  (case-insensitive: `Ara`, `Eve`, `Leo`, `Rex`, `Sal`) AND custom
  voice IDs (the 8-character lowercase alphanumeric IDs returned by
  `CustomVoiceClient.create_voice()`). The `voice_id` parameter
  flows through unchanged into the LiveKit / xAI realtime layer —
  xAI accepts either form on the same endpoint.

  ```python
  from grok_faf_voice import VoiceAgent
  VoiceAgent(voice="nlbqfwie").run()  # custom-cloned voice
  ```

- **`examples/hello_custom_voice.py`** — end-to-end demo showing
  voice clone → `VoiceAgent` integration in the same two-line shape.

### Changed

- **Validation error message expanded** to guide users to xAI's
  Custom Voices docs when an unrecognized voice is passed. Built-in
  voice acceptance is now case-insensitive at the SDK boundary
  (matches xAI API behavior).

### Notes

- `XAI_API_KEY` is the only credential needed for `CustomVoiceClient`
  — same key as `VoiceAgent`. Voice IDs you create persist on your
  xAI account and can be reused across sessions, devices, and SDK
  versions.
- The 30 free voices available via the [xAI console](https://console.x.ai)
  work too — pass their `voice_id` directly to `VoiceAgent(voice=...)`
  or `CustomVoiceClient.text_to_speech()`.
- No breaking changes to the v0.1.3 surface. Existing two-line
  `VoiceAgent().run()` code keeps working unchanged.

## [0.1.3] — 2026-04-30 — VoiceAgent zero-config

> Note: v0.1.2 was tagged internally during development but never
> published to PyPI. v0.1.3 is the public follow-up to v0.1.1.


### Added

- **`VoiceAgent` — the two-line shape:**
  ```python
  from grok_faf_voice import VoiceAgent
  VoiceAgent().run()
  ```
  First run silently provisions an anonymous identity (namepoint +
  Voice key) via `mcpaas.live` and persists it at
  `~/.grok-faf-voice/identity.json` (mode `0600`). Subsequent runs
  load the identity and the agent picks up every session knowing
  what was etched in past ones. Only `XAI_API_KEY` is required;
  LiveKit cloud env vars are optional (`console` mode runs locally).
- **`VoiceAgentConfigError`** — actionable error class (missing
  `XAI_API_KEY`, malformed identity file, unsupported voice, etc.).
- **Identity precedence** — kwargs → env vars → local file →
  anonymous provisioning. Power mode (`api_key=`, `namepoint=`) is
  opt-in; existing `FAFMemory` / `FAFContext` primitives unchanged.
- **Voice Memory Layer (VML)** — coined as the FAF-family term for
  voice-memory persistence. Extension `.fafm` (family mark
  `.fafm 🐘🎙️`). Media type `application/vnd.fafm+yaml` planned
  for IANA registration. This SDK is the reference implementation.
- **`examples/hello.py`** — the canonical two-line demo.

### Changed

- **README rewritten around the Fast⚡️AF memory setup.** Hero is
  the two-line install. Namepoint introduced as your `@handle` for
  AI memory (like `@username` on X). Advanced setup details
  (`FAFMemory`, custom ledgers, env-var configuration, retention
  tiers) link out to [mcpaas.live/voice/about](https://mcpaas.live/voice/about).
- **Renamed `token` → `api_key` across the SDK** (breaking,
  pre-1.0, zero installed users). Env var: `MCPAAS_TOKEN` →
  `MCPAAS_API_KEY`. Constructor kwarg: `FAFMemory(soul, token=...)`
  → `FAFMemory(soul, api_key=...)`. Matches xAI / LiveKit
  ecosystem convention.

### Fixed

- **Etch discipline — agent no longer etches meta-statements.**
  The `etch_memory` tool description previously said "Use when the
  user says 'remember this'" without distinguishing intent from
  substance, so the model would fire the tool on user intent
  ("I want to make a memory") and stuff the meta-statement into
  the soul. Three etches to capture one fact. Tool description
  now requires the substance itself; ETCH DISCIPLINE rules added
  to `LATENCY_BRIDGE_INSTRUCTIONS` reinforce: ask "what would you
  like me to remember?" when the user states intent without
  content, then etch only the content.

### Notes

- Identity file location: `~/.grok-faf-voice/identity.json` (0600).
- Anonymous identities are non-recoverable by design — SDK is the
  system of record. For recoverability, claim a namepoint at
  [mcpaas.live/voice/setup](https://mcpaas.live/voice/setup).
- Retention tiers (90-day sliding for anonymous, 365-day for
  email-claimed, unlimited with namepoint subscription) ride on
  existing namepoint pricing — no separate Voice Pro SKU.

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
