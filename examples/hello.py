"""Hello, Grok — the two-line shape.

Run::

    pip install grok-faf-voice
    python examples/hello.py console

That's it. First run provisions an anonymous identity silently
(saved to ``~/.grok-faf-voice/identity.json``). Every subsequent
run picks up where the last one left off — close, come back,
the agent remembers what was etched.

The only env var you need is ``XAI_API_KEY``. LiveKit cloud env
vars (``LIVEKIT_*``) are only needed if you deploy via
``python examples/hello.py start``; ``console`` mode runs locally.

Want to brand your namepoint or share memory across machines? See
the Advanced section of the README.
"""

from grok_faf_voice import VoiceAgent

VoiceAgent().run()
