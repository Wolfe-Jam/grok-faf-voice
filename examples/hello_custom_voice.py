"""Hello, Grok — with a cloned voice.

xAI shipped Custom Voices on 2026-05-01. This example shows the
zero-config two-line shape, with your own cloned voice.

Run::

    pip install grok-faf-voice

    # 1. Record a 90-120s WAV sample reading xAI's verification
    #    passphrase. Save as ``my_sample.wav``.
    # 2. Set $XAI_API_KEY (https://x.ai/api).
    # 3. Run:
    python examples/hello_custom_voice.py console

The first half clones a voice via xAI's Custom Voices API. The
second half is the canonical zero-config two-line agent — but
speaking in your cloned voice instead of one of the five built-ins.

If you've already cloned a voice and have the ``voice_id`` (8-char
lowercase alphanumeric, e.g. ``nlbqfwie``), skip the clone step
and pass it directly to ``VoiceAgent(voice=...)``.
"""

from grok_faf_voice import CustomVoiceClient, VoiceAgent

# ── 1. Clone the voice (one-time, persisted on your xAI account) ─────────────
cv = CustomVoiceClient()  # uses $XAI_API_KEY
voice = cv.create_voice(
    "my_sample.wav",
    name="My Voice",
    description="Cloned from a 90-second sample",
    language="en",
)
print(f"Cloned voice_id: {voice['voice_id']}")

# ── 2. Run the agent with the cloned voice — memory still permanent ──────────
VoiceAgent(voice=voice["voice_id"]).run()
