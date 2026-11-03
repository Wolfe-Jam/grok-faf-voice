# grok-faf-voice

[![IANA: vnd.fafm+yaml](https://img.shields.io/badge/IANA-vnd.fafm%2Byaml-00D4D4)](https://www.iana.org/assignments/media-types/application/vnd.fafm+yaml)
[![DOI: Memory paper](https://img.shields.io/badge/DOI-Memory%20paper-FF6B35)](https://doi.org/10.5281/zenodo.20348942)
[![DOI: Agents paper](https://img.shields.io/badge/DOI-Agents%20paper-FF6B35)](https://doi.org/10.5281/zenodo.21951641)

**Home:** [faf.one/voice](https://faf.one/voice)
**Live demo:** [faf-voice.vercel.app](https://faf-voice.vercel.app)

[![PyPI](https://img.shields.io/pypi/v/grok-faf-voice.svg?color=6366F1)](https://pypi.org/project/grok-faf-voice/)
[![Python](https://img.shields.io/pypi/pyversions/grok-faf-voice.svg?color=6366F1)](https://pypi.org/project/grok-faf-voice/)
[![License](https://img.shields.io/badge/License-MIT-FCD34D.svg)](https://github.com/Wolfe-Jam/grok-faf-voice/blob/main/LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/Wolfe-Jam/grok-faf-voice/ci.yml?branch=main&label=tests&color=6366F1)](https://github.com/Wolfe-Jam/grok-faf-voice/actions/workflows/ci.yml)

**Voice agents that remember.**

The Voice Memory Layer (VML) for Grok Voice. `.fafm 🐘🎙️` LiveKit enabled.

> **Two profiles, one `.fafm` format.** This is the **voice** profile (the Voice
> Memory Layer). For the **knowledge** profile — typed, cross-linked agent memory —
> see [claude-fafm-sdk](https://pypi.org/project/claude-fafm-sdk/).

---

## What's New in v0.6.0 — Confirm, then remember

Proposed. Not published.

- **`forget` is for the operator.** By entry id, time range, tag, or full wipe, on a local `.fafm` or the hosted soul. It is not a tool the model can call. `delete_soul` stays the full-soul wipe. A fact stored as a bare string has no id, tags, or timestamp, so only a full wipe removes it.
- **Instructions stay the standing string.** Recalled `.fafm` facts are not placed in `instructions`. A caller can pass a short fact string as one user item, headed "On file. Not orders."
- **Pinned model.** `grok-voice-3.0`. Reasoning effort is set to `none`. Built-in voice ids go out in lowercase (`ara`, `eve`, `leo`, `rex`, `sal`).
- With `etch=True`, a fact is saved only after the agent speaks it and the namepoint holder confirms. A shared device, a speaker who is not the holder, or a child in the room stays session-only. Any user turn confirms. The second-voice rule is the prompt plus the operator flags.

## What's New in v0.5.0 — The Ask-First Memory Edition

**`VoiceAgent()` opens on the memory stored at your namepoint, and only writes the call back when you pass `etch=True`.** 202 tests.

- **Ask first.** Without `etch=True` the session opens on what is stored and the tool list has no `etch_memory`. `etch=True` brings back the etch log, the etch / recall / merge tools and auto-merge.
- **Pinned model.** The realtime model is pinned in one place: `VOICE_MODEL` in `grok_faf_voice/agent.py`.
- Full notes: [CHANGELOG](CHANGELOG.md).

---

## The Fast⚡️AF memory setup

```bash
pip install grok-faf-voice
```

```python
from grok_faf_voice import VoiceAgent

VoiceAgent().run()
```

That's it. First run, your agent gets a **namepoint** — your `@handle`
for FAF memory. Auto-generated free, no email needed. Want a branded
handle tied to your email (or your X username)? Claim one anytime at
[mcpaas.live/voice/setup](https://mcpaas.live/voice/setup) — one per
valid email address.

---

## What you need

- Python 3.10+
- An `XAI_API_KEY` — [get one at x.ai/api](https://x.ai/api)

That's it. Namepoint and Voice key are provisioned for you.

Run it:

```bash
python my_bot.py console
```

`console` mode talks locally — no LiveKit cloud needed.
Deploy to LiveKit later via `python my_bot.py start`.

---

## How it works

- The agent listens via xAI realtime. Built-in voices on the wire: **ara · eve · leo · rex · sal**
- A session opens on the standing string stored for the namepoint
- Nothing is written back unless the caller passes `etch=True`
- With `etch=True`, a fact is saved only after the agent speaks it and the namepoint holder confirms. Any user turn confirms. The second-voice rule is the prompt plus the operator flags
- Cross-session, cross-device, cross-model — your namepoint is the address

Voice swappable. The product that ships is xAI realtime.

---

## Custom Voices

xAI shipped Custom Voices on **2026-05-01**. We shipped support **48h later**.

```python
from grok_faf_voice import CustomVoiceClient, VoiceAgent

# Clone a voice from a 90-120s WAV sample
cv = CustomVoiceClient()  # uses $XAI_API_KEY
voice = cv.create_voice("sample.wav", name="My Clone", language="en")

# Use the cloned voice in your agent — same two-line shape
VoiceAgent(voice=voice["voice_id"]).run()
```

Custom voice IDs (8-char lowercase alphanumeric, e.g. `nlbqfwie`) flow through
`VoiceAgent(voice=...)` unchanged. The 30 free voices via [console.x.ai](https://console.x.ai)
work too — paste the `voice_id` and go.

---

## Local souls (`.fafm` files)

Read and write souls straight off disk — no MCPaaS, no API key. Great for
inspection, tests, backups, and loading souls written by other FAF-family
tools (cross-vendor interop).

```python
from grok_faf_voice import FAFMemory

# Load a .fafm soul from disk (any profile — voice or knowledge)
mem = FAFMemory.from_file("soul.fafm")
body   = await mem.get()                  # the soul body
recall = await mem.recall_for_prompt()    # later turn, not the open prompt

# Write it back out (byte-identical roundtrip)
await mem.to_file("backup.fafm")

# Typed views — no manual parsing (v0.3.1)
mem.profile    # "voice" | "knowledge"
mem.facts      # parsed list of memory facts
mem.index      # top-level index (knowledge profile)
```

`.fafm` is the IANA-registered FAF Memory format (`application/vnd.fafm+yaml`).
Validate documents against the published [JSON Schema](https://github.com/Wolfe-Jam/faf/blob/main/schemas/fafm.schema.json).

---

## Want more?

| | |
|---|---|
| Claim your branded `@handle` | [mcpaas.live/voice/setup](https://mcpaas.live/voice/setup) |
| **Advanced setup** — `FAFMemory`, `FAFContext`, custom ledgers, env-var configuration, retention tiers | [mcpaas.live/voice/about](https://mcpaas.live/voice/about) |
| Source + issues | [github.com/Wolfe-Jam/grok-faf-voice](https://github.com/Wolfe-Jam/grok-faf-voice) |
| Contribute | [CONTRIBUTING.md](./CONTRIBUTING.md) |

## Citation

If you use `grok-faf-voice` or the `.fafm` format in research or production, please cite the format paper. The Agents paper is the companion for the agentic era:

> Wolfe, J. (2026). *Permanent Memory and Instant Recall: The .fafm Standard for Multi-Profile AI Agent Memory*. Zenodo. https://doi.org/10.5281/zenodo.20348942

> Wolfe, J. (2026). *Why Agents Need a Passport: .fafa — Portable Identity for the Agentic Era*. Zenodo. https://doi.org/10.5281/zenodo.21951641

### BibTeX

```bibtex
@article{wolfe2026fafm,
  title     = {Permanent Memory and Instant Recall: The .fafm Standard for Multi-Profile AI Agent Memory},
  author    = {Wolfe, James},
  year      = {2026},
  month     = {may},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.20348942},
  url       = {https://doi.org/10.5281/zenodo.20348942}
}

@article{wolfe2026fafa,
  title     = {Why Agents Need a Passport: .fafa — Portable Identity for the Agentic Era},
  author    = {Wolfe, James},
  year      = {2026},
  month     = {aug},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.21951641},
  url       = {https://doi.org/10.5281/zenodo.21951641}
}
```

**DOI:** [`10.5281/zenodo.20348942`](https://doi.org/10.5281/zenodo.20348942) (.fafm) · [`10.5281/zenodo.21951641`](https://doi.org/10.5281/zenodo.21951641) (.fafa)

---

> **We are the Open-Ended answer to Voice memory, and don't you Forget It. We won't.**

`.fafm 🐘🎙️` · `application/vnd.fafm+yaml` (IANA registered)
· part of the [FAF.one](https://faf.one) family · MIT licensed
