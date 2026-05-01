# grok-faf-voice

[![PyPI](https://img.shields.io/pypi/v/grok-faf-voice.svg?color=6366F1)](https://pypi.org/project/grok-faf-voice/)
[![Python](https://img.shields.io/pypi/pyversions/grok-faf-voice.svg?color=6366F1)](https://pypi.org/project/grok-faf-voice/)
[![License](https://img.shields.io/badge/License-MIT-FCD34D.svg)](https://github.com/Wolfe-Jam/grok-faf-voice/blob/main/LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/Wolfe-Jam/grok-faf-voice/ci.yml?branch=main&label=tests&color=6366F1)](https://github.com/Wolfe-Jam/grok-faf-voice/actions/workflows/ci.yml)

**Voice agents that remember.**

The Voice Memory Layer (VML) for Grok Voice. `.fafm 🐘🎙️` LiveKit enabled.

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

- The agent listens via xAI realtime — five built-in voices: **Ara · Eve · Leo · Rex · Sal**
- Every session opens already remembering what was etched in past ones
- At session end, new memories consolidate silently
- Cross-session, cross-device, cross-model — your namepoint is the address

Voice swappable, memory permanent. ElevenLabs and Hume land in upcoming releases.

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

## Want more?

| | |
|---|---|
| Claim your branded `@handle` | [mcpaas.live/voice/setup](https://mcpaas.live/voice/setup) |
| **Advanced setup** — `FAFMemory`, `FAFContext`, custom ledgers, env-var configuration, retention tiers | [mcpaas.live/voice/about](https://mcpaas.live/voice/about) |
| Source + issues | [github.com/Wolfe-Jam/grok-faf-voice](https://github.com/Wolfe-Jam/grok-faf-voice) |
| Contribute | [CONTRIBUTING.md](./CONTRIBUTING.md) |

---

> **We are the Open-Ended answer to Voice memory, and don't you Forget It. We won't.**

`.fafm 🐘🎙️` · `application/vnd.fafm+yaml` (IANA registration planned)
· part of the [FAF.one](https://faf.one) family · MIT licensed
