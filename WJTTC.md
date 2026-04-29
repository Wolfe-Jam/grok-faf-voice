# WJTTC — `grok-faf-voice` test regime

**F1-inspired, championship-grade. Four tiers. Built to survive
fast-moving upstreams.**

xAI Voice (Realtime + Standalone TTS, 5 voices, expressive tags) and
LiveKit Agents (`AgentServer`, `AgentSession`, `JobContext`,
`@function_tool`) are both in active drift. This regime exists so
breakage shows up here, in our test suite, **before** a user hits it.

---

## Tiers

| Tier | Cost | Trigger | Job |
|------|------|---------|-----|
| 🛑 **BRAKE** | ~10s, free | every commit / PR | Hard gates. If a brake fails, the wheel does not ship. |
| ⚙️ **ENGINE** | ~5s, free | every commit / PR | Correctness + upstream-contract pins. Catches drift before users do. |
| 🌀 **AERO** | ~2s, free | every commit / PR | Polish. Wheel inventory, no junk in dist, version sync. |
| 🛞 **TYRES** | ~30–60s, costs $ + creds | weekly cron / pre-release / manual | Live probes against xAI + MCPaaS. The early-warning heartbeat. |

---

## 🛑 BRAKE — gating checks

Hard requirements before a release. Run on every commit. Fail = stop.

- `pytest --no-cov -q` — full suite passes (113+ tests, 3 network-marker skips)
- `ruff check grok_faf_voice tests` — lint clean
- `python -m build` — sdist + wheel build cleanly
- `twine check dist/*` — both pass PyPI's metadata validator
- Version sync — `pyproject.toml` and `grok_faf_voice/__init__.py` agree

---

## ⚙️ ENGINE — correctness + upstream-contract pins

The reason the SDK exists: regression tests for known bugs, plus
**pinned surface shapes** for every upstream we depend on. If LiveKit
or xAI renames a symbol or changes a kwarg, ENGINE goes red.

### Regressions

- `test_attach_auto_merge_resets_flags_for_session_reuse` — v0.1.1's
  fix: reusing one `FAFMemory` across sessions does not cause session
  2+'s shutdown merge to no-op.

### Upstream contracts pinned (`tests/test_wjttc_contracts.py`)

**LiveKit Agents** — the import surface we depend on:

- `from livekit.agents import Agent, AgentServer, AgentSession, JobContext, RunContext, function_tool`
- `JobContext.add_shutdown_callback` exists
- `AgentServer.rtc_session` is a callable decorator
- `function_tool` is callable

**xAI plugin (`livekit.plugins.xai`)**:

- `xai.realtime.RealtimeModel` importable
- `RealtimeModel.__init__` accepts `voice` and `turn_detection` kwargs

**xAI chat completions / structured outputs** — the merge-engine path:

- `XAI_CHAT_URL == "https://api.x.ai/v1/chat/completions"`
- Request body shape: `response_format.type == "json_schema"`, `strict: true`

**MCPaaS** — the persistence backend:

- `MCPAAS_URL == "https://mcpaas.live/mcp"`
- `MCPAAS_RAW_URL == "https://mcpaas.live/raw/{slug}"`
- Tool names `write_soul` and `get_soul` referenced in source

**fastmcp**:

- `fastmcp.Client.call_tool` exists
- `fastmcp.exceptions.ToolError` is an `Exception` subclass

**Pydantic / structured-output schema**:

- `MergeResult.model_json_schema()` emits a `decisions` property — the
  shape xAI strict mode requires.

---

## 🌀 AERO — polish

Non-blocking but a championship-grade ship always checks.

- Coverage delta on `grok_faf_voice/memory.py` (the load-bearing module — keep ≥ 90%)
- Wheel inventory — no `tests/`, no `.bak`, no `.env`, no `examples/`
  in `dist/*.whl`
- Sdist inventory — `tests/`, `README.md`, `LICENSE`, `pyproject.toml`
  all present in `dist/*.tar.gz`

---

## 🛞 TYRES — live probes

These cost real money and require credentials. They do **not** gate
every commit. Run them manually before a release, or on a weekly cron.

Skipped (not failed) when env vars are missing — a missing
`XAI_API_KEY` is "we can't probe right now", not "the upstream is
broken".

- **xAI realtime model probe** — POST a 1-token request to
  `https://api.x.ai/v1/chat/completions` with model
  `grok-4-1-fast-reasoning` and `response_format.json_schema.strict =
  true`. Assert HTTP 200. If structured outputs strict mode is
  removed, this fails.
- **MCPaaS public-soul probe** — `get_soul` against a known public
  soul (`grok` or `faf`). Assert non-empty body. If MCPaaS is down or
  protocol drifts, this fails.
- **LiveKit pip resolution** — `pip install --dry-run
  'livekit-agents[xai]>=1.4'` resolves cleanly.

Future probes (when their endpoints stabilize):

- xAI Standalone TTS expressive-tag round-trip (`<sing>`, `<whisper>`)
- xAI voice ID enumeration endpoint (currently fixed at 5: Ara, Eve,
  Leo, Rex, Sal — verify quarterly)

---

## How to run

```bash
# Default — BRAKE + ENGINE + AERO. The PR gate.
./scripts/wjttc.sh

# Add live probes — pre-release or weekly.
./scripts/wjttc.sh --tyres

# TYRES probes only — fastest health check of the upstream world.
./scripts/wjttc.sh --tyres-only

# Help
./scripts/wjttc.sh --help
```

The script exits **0** when every tier it ran passed, **1**
otherwise. TYRES skips count as 0 (skip ≠ fail).

---

## Release history

| Version | Date | BRAKE | ENGINE | AERO | TYRES | Notes |
|---------|------|-------|--------|------|-------|-------|
| v0.1.2 | 2026-04-29 | ✅ | ✅ | ✅ | ✅ | VML brand + sibling alignment + onboarding. TYRES verified live (xAI strict-mode + MCPaaS public-soul + LiveKit pip resolution). |
| v0.1.1 | 2026-04-29 | ✅ | ✅ | ✅ | — | Auto-merge session-reuse fix. WJTTC v1 introduced. TYRES not yet wired. |

---

## Why this exists

The SDK sits between two upstreams whose APIs are weeks old, not
years. xAI Voice split into Realtime + Standalone TTS in March 2026.
LiveKit Agents added the `JobContext.add_shutdown_callback` model
recently. The cost of a silent break is high — voice agents fail
loudly and visibly to end users. The cost of a pinned contract test
is two seconds.

**No "Guaranteed". No "always works". Just brakes that hold, an
engine that verifies, aero that polishes, and tyres that warn early.**
