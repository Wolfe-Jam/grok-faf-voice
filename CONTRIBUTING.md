# Contributing

Contributions are welcome. Bug fixes, doc improvements, new
`VoiceProvider` backends, new `Consolidator` strategies — all useful.

This file describes **how to land a change cleanly**. For first-time
setup, see [ONBOARDING.md](ONBOARDING.md) — the five-minute path
from empty terminal to a running voice agent.

---

## Before opening a PR

Run the full test regime:

```bash
./scripts/wjttc.sh
```

If it ends with **🏎️ Championship pace.** you're good. Any 🚨 line means
fix before pushing — see [WJTTC.md](WJTTC.md) for what each tier
guards against.

PRs that don't pass 🛑 BRAKE will not be reviewed.

---

## PR conventions

| Type of change | Required |
|---|---|
| Bug fix | A regression test that fails on the bug, passes after the fix. (See `test_attach_auto_merge_resets_flags_for_session_reuse` for the v0.1.1 reference shape — the test came **with** the fix, not after.) |
| New feature | Tests for the new surface. ENGINE-tier contract pin if it touches an upstream API (LiveKit, xAI, MCPaaS, fastmcp, Pydantic). |
| Doc-only | No tests required. WJTTC `🛑 BRAKE` still gates (lint + build + version sync). |
| Refactor | Existing tests must pass unchanged. Coverage on `memory.py` must stay ≥ 90%. |

`ruff check grok_faf_voice tests` must be clean. The ruff config in
`pyproject.toml` is authoritative.

---

## Branch model

- `main` is always shippable. Tagged releases come from `main`.
- Work on feature branches. PR → squash-merge into `main`.
- Don't open PRs against tagged commits. Tags are immutable; any
  fix lands on `main` and gets a new tag if it's release-worthy.

---

## Commit messages

- Imperative mood: "fix: reset auto-merge flags" not "fixed" / "fixes".
- Conventional-commits prefix is appreciated but not enforced
  (`fix:` / `feat:` / `refactor:` / `chore:` / `docs:` / `test:`).
- Body explains the **why** when it's non-obvious. The diff explains
  the what.
- No marketing language in commit subjects. "Bible-grade" and
  similar are out — F1-inspired is the project tone, but commit
  messages are technical, not promotional.

---

## Code style

- **Names over comments.** A well-named function or variable
  doesn't need a comment explaining what it does.
- **WHY-comments are welcome** where the *why* isn't obvious — a
  hidden constraint, a workaround for a specific bug, a non-obvious
  invariant. See `memory.py`'s `attach_auto_merge` block for the
  reference shape.
- **No marketing prose in code comments.** Internal docs are
  documentation, not pitch material.
- Type hints on public signatures. Internal helpers can be looser.
- `from __future__ import annotations` at the top of any new module
  using `|` union types — keeps Python 3.10 compatibility.

---

## CI doctrine

Two rules from the project's CI philosophy
(`.github/workflows/ci.yml`):

1. **Red means real.** A red `test` job is a real, actionable
   failure. We don't tolerate flaky tests — if a test fails
   intermittently, that's a bug in the test, fix it before merging
   anything else.
2. **Lint is observability, not a gate.** Ruff failures show up but
   don't block merges. Lint cleanup PRs are welcome but never
   urgent. Tests are the only gate.

If CI goes red after a merge, the breaking change owns the
fix — revert is on the table, no shame.

---

## Adding a new upstream dependency

If you add a new `import` from LiveKit, xAI, MCPaaS, fastmcp,
Pydantic, or any other moving upstream:

1. Pin the surface shape in `tests/test_wjttc_contracts.py` — the
   ENGINE-tier guards against silent drift.
2. If the import is conditional or optional, document why in a
   module-level comment.
3. Update the WJTTC release-history table if the addition is
   release-worthy.

The contract-pin discipline is what keeps the SDK shippable as
xAI / LiveKit ship breaking changes weekly.

---

## Adding a new VoiceProvider (v0.3.0+)

Once `VoiceProvider` Protocol lands in v0.3.0, contributing a new
backend (ElevenLabs / Hume / custom cloning) is the highest-value
contribution path. Each new provider:

- Implements the `VoiceProvider` Protocol
- Lives in `grok_faf_voice/providers/<name>.py`
- Ships with one round-trip integration test (gated as `network`)
- Gets a CHANGELOG entry under the next minor release
- Doesn't change `FAFMemory` or any other memory-layer internals
  (memory stays opinionated to MCPaaS — see the architecture
  decision below)

---

## Architecture decisions

The SDK has two firm design rules that aren't up for debate in PRs:

1. **Voice provider is pluggable.** xAI today, more ahead. PRs
   adding new `VoiceProvider` impls are welcome.
2. **Memory store is NOT pluggable.** MCPaaS via `FAFMemory` is the
   path. PRs proposing `LocalMemoryStore` / `S3MemoryStore` / other
   backends will be closed. Memory is the moat — abstracting it
   dilutes the SDK's distinct value. Devs needing a different store
   have other tools (LiveKit's session state, custom MCP servers).

---

## Where to file issues

[github.com/Wolfe-Jam/grok-faf-voice/issues](https://github.com/Wolfe-Jam/grok-faf-voice/issues)

For security issues: don't open a public issue. Email
**team@faf.one** with details.

---

## License

MIT. Fork it, ship it, embed it, enjoy it.

**Don't copy FAF brand. Do your own.**
